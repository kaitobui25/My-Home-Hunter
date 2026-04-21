"""
src/scraper/rental_hunter.py
Scrapes SUUMO chintai (rental) listing pages with full pagination support.
URL pattern: /jj/chintai/ichiran/FR301FC001/
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from src.scraper.base import AbstractHunter, PlaywrightBase
from src.config import GeneralConfig, SearchConfig

logger = logging.getLogger(__name__)


class SUUMORentalHunter(AbstractHunter, PlaywrightBase):
    """Scrapes rental listings from SUUMO chintai pages."""

    def __init__(self, search: SearchConfig, general: GeneralConfig):
        AbstractHunter.__init__(self, search_name=search.name)
        PlaywrightBase.__init__(
            self,
            webdriver_path=general.webdriver_path,
            headless=general.headless,
            disable_images_css=general.disable_images_css,
        )
        self.start_url = search.url
        self.page_load_timeout = general.page_load_timeout * 1000  # Playwright timeout in ms
        self.delay_between_pages = general.delay_between_pages
        self.max_pages = general.max_pages_per_search

    def scrape(self) -> list[dict]:
        all_listings = []
        current_url = self.start_url
        page_num = 1

        try:
            while current_url and page_num <= self.max_pages:
                logger.info("[%s] Scraping page %d: %s", self.search_name, page_num, current_url)
                page_listings = self._scrape_page(current_url, page_num)
                all_listings.extend(page_listings)
                logger.info(
                    "[%s] Page %d: %d rooms found (total so far: %d)",
                    self.search_name, page_num, len(page_listings), len(all_listings),
                )

                current_url = self._get_next_page_url()
                if current_url:
                    page_num += 1
                    time.sleep(self.delay_between_pages)
        except Exception as e:
            logger.error("[%s] Scraping failed: %s", self.search_name, e, exc_info=True)
        finally:
            self.close_driver()

        return all_listings

    def _scrape_page(self, url: str, page_num: int) -> list[dict]:
        try:
            self.page.goto(url, timeout=self.page_load_timeout)
        except Exception as e:
            logger.warning("[%s] Page %d goto failed: %s", self.search_name, page_num, e)
            return []

        # Wait for listings container
        try:
            self.page.wait_for_selector(".cassetteitem", timeout=self.page_load_timeout)
        except Exception:
            logger.warning("[%s] Page %d: timeout waiting for listings.", self.search_name, page_num)
            return []

        listings = []
        buildings = self.page.query_selector_all(".cassetteitem")

        for building in buildings:
            listings.extend(self._parse_building(building))

        return listings

    def _parse_building(self, building) -> list[dict]:
        results = []
        try:
            title_elem = building.query_selector(".cassetteitem_content-title")
            addr_elem = building.query_selector(".cassetteitem_detail-col1")
            trans_elem = building.query_selector(".cassetteitem_detail-col2")
            
            building_name = title_elem.inner_text().strip() if title_elem else ""
            address = addr_elem.inner_text().strip() if addr_elem else ""
            transportation = trans_elem.inner_text().strip() if trans_elem else ""
        except Exception:
            return results

        rows = building.query_selector_all("tr.js-cassette_link")
        for row in rows:
            listing = self._parse_room_row(row, building_name, address, transportation)
            if listing:
                results.append(listing)

        return results

    def _parse_room_row(self, row, building_name: str, address: str, transportation: str) -> dict | None:
        try:
            floor_elem = row.query_selector("td:nth-child(3)")
            rent_elem = row.query_selector(".cassetteitem_other-emphasis")
            admin_fee_elem = row.query_selector(".cassetteitem_price--administration")
            deposit_elem = row.query_selector(".cassetteitem_price--deposit")
            key_money_elem = row.query_selector(".cassetteitem_price--gratuity")
            layout_elem = row.query_selector(".cassetteitem_madori")
            size_elem = row.query_selector(".cassetteitem_menseki")
            url_elem = row.query_selector("a.cassetteitem_other-linktext")

            floor_raw = floor_elem.inner_text().strip() if floor_elem else ""
            rent_raw = rent_elem.inner_text().strip() if rent_elem else ""
            admin_fee_raw = admin_fee_elem.inner_text().strip() if admin_fee_elem else ""
            deposit_raw = deposit_elem.inner_text().strip() if deposit_elem else ""
            key_money_raw = key_money_elem.inner_text().strip() if key_money_elem else ""
            layout = layout_elem.inner_text().strip() if layout_elem else ""
            size_raw = size_elem.inner_text().strip() if size_elem else ""
            url = url_elem.get_attribute("href") if url_elem else ""

            # Try to get building age
            try:
                age_elem = row.query_selector(".cassetteitem_detail-col3 div")
                age_text = age_elem.inner_text().strip() if age_elem else ""
            except Exception:
                age_text = ""

            # Try to get image
            image_url = None
            try:
                img = row.query_selector(".casssetteitem_other-thumbnail-img") 
                if img:
                    image_url = img.get_attribute("src")
            except Exception:
                pass

            # Make url absolute if necessary
            if url and url.startswith("/"):
                url = "https://suumo.jp" + url

            return {
                "name": building_name,
                "listing_type": "rental",
                "price_raw": rent_raw,
                "price_man_yen": _parse_man_yen(rent_raw),
                "admin_fee_raw": admin_fee_raw,
                "admin_fee_yen": _parse_yen(admin_fee_raw),
                "deposit_raw": deposit_raw,
                "deposit_man_yen": _parse_man_yen(deposit_raw),
                "key_money_raw": key_money_raw,
                "key_money_man_yen": _parse_man_yen(key_money_raw),
                "layout": layout,
                "size_m2": _parse_m2(size_raw),
                "size_raw": size_raw,
                "floor": floor_raw,
                "floor_num": _parse_floor_num(floor_raw),
                "building_age": _parse_building_age(age_text),
                "building_age_raw": age_text,
                "address": address,
                "transportation": transportation,
                "url": url,
                "image_url": image_url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.debug("Failed to parse row: %s", e)
            return None

    def _get_next_page_url(self) -> str | None:
        try:
            next_btn = self.page.query_selector("p.pagination-parts a:has-text('次へ')")
            if next_btn:
                href = next_btn.get_attribute("href")
                if href and href.startswith("/"):
                    return "https://suumo.jp" + href
                return href
        except Exception:
            pass
        return None

# Parse helpers
def _parse_man_yen(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*万円", text)
    if match:
        return float(match.group(1))
    return None

def _parse_yen(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"([\d,]+)\s*円", text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None

def _parse_m2(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*(?:m|㎡)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None

def _parse_floor_num(text: str) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d+)\s*階", text)
    if match:
        return int(match.group(1))
    return None

def _parse_building_age(text: str) -> int | None:
    if not text:
        return None
    if "新築" in text:
        return 0
    match = re.search(r"築(\d+)年", text)
    if match:
        return int(match.group(1))
    return None
