"""
src/scraper/sale_hunter.py
Scrapes SUUMO bukken (sale/land) listing pages.
URL pattern: /jj/bukken/ichiran/JJ012FC003/
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from src.scraper.base import AbstractHunter, PlaywrightBase
from src.config import GeneralConfig, SearchConfig

logger = logging.getLogger(__name__)


class SUUMOSaleHunter(AbstractHunter, PlaywrightBase):
    """Scrapes sale/land listings from SUUMO bukken pages."""

    def __init__(self, search: SearchConfig, general: GeneralConfig):
        AbstractHunter.__init__(self, search_name=search.name)
        PlaywrightBase.__init__(
            self,
            webdriver_path=general.webdriver_path,
            headless=general.headless,
            disable_images_css=general.disable_images_css,
        )
        self.start_url = search.url
        self.page_load_timeout = general.page_load_timeout * 1000
        self.delay_between_pages = general.delay_between_pages
        self.max_pages = general.max_pages_per_search

    def scrape(self) -> list[dict]:
        all_listings = []
        current_url = self.start_url
        page_num = 1

        try:
            while current_url and page_num <= self.max_pages:
                logger.info("[%s] Scraping page %d", self.search_name, page_num)
                page_listings = self._scrape_page(current_url, page_num)
                all_listings.extend(page_listings)
                logger.info(
                    "[%s] Page %d: %d listings (total: %d)",
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

        try:
            self.page.wait_for_selector("#right_sliderList2", timeout=self.page_load_timeout)
        except Exception:
            logger.warning("[%s] Page %d: timeout.", self.search_name, page_num)
            return []

        listings = []
        items = self.page.query_selector_all("#right_sliderList2 li[id^='jsiRightSliderListChild_']")

        for item in items:
            listing = self._parse_item(item)
            if listing:
                listings.append(listing)

        return listings

    def _parse_item(self, item) -> dict | None:
        try:
            name_elem = item.query_selector("p a")
            property_name = name_elem.inner_text().strip() if name_elem else "Unknown"
            url = name_elem.get_attribute("href") if name_elem else ""
            if url and url.startswith("/"):
                url = "https://suumo.jp" + url

            # Price
            price_raw = "Not found"
            price_per_tsubo = "Not found"
            price_elements = item.query_selector_all("div.fr.w105.bw p:has-text('円')")
            for elem in price_elements:
                text = elem.inner_text()
                if "万円" in text and price_raw == "Not found":
                    price_raw = text.strip()
                elif "坪単価" in text:
                    m = re.search(r"[\d.]+万円", text)
                    if m:
                        price_per_tsubo = m.group()

            # Size
            try:
                size_elem = item.query_selector("div.fr p:nth-of-type(2)")
                size_raw = size_elem.inner_text() if size_elem else "Not Available"
                size_raw = size_raw.replace("土地／", "").replace("㎡", "m2").strip()
                size_raw = re.sub(r"<[^>]+>", "", size_raw)
            except Exception:
                size_raw = "Not Available"

            # Building coverage / floor area ratios
            try:
                ratios_elem = item.query_selector("div.fr.w105.bw p:has-text('建ぺい率・容積率')")
                if ratios_elem:
                    _, combined = ratios_elem.inner_text().split("／")
                    bcr, far = combined.split("\u3000")  # fullwidth space
                    building_coverage_ratio = bcr.strip()
                    floor_area_ratio = far.strip()
                else:
                    raise ValueError
            except Exception:
                building_coverage_ratio = "N/A"
                floor_area_ratio = "N/A"

            # Features
            feature_elems = item.query_selector_all("ul.cf li")
            features = "\n".join(e.inner_text() for e in feature_elems)

            # Transportation
            try:
                trans_elem = item.query_selector("p.mt5:nth-of-type(2)")
                transportation = trans_elem.inner_text().strip() if trans_elem else "N/A"
            except Exception:
                transportation = "N/A"

            # Image
            try:
                img_elem = item.query_selector(".fl.w90 img")
                if img_elem:
                    img_url = img_elem.get_attribute("src")
                    img_url = re.sub(r"&w=\d+&h=\d+", "&w=500&h=500", img_url) if img_url else None
                else:
                    img_url = None
            except Exception:
                img_url = None

            return {
                "name": property_name,
                "listing_type": "sale",
                "price_raw": price_raw,
                "price_man_yen": _parse_man_yen(price_raw),
                "admin_fee_raw": "",
                "admin_fee_yen": None,
                "deposit_raw": "",
                "deposit_man_yen": None,
                "key_money_raw": "",
                "key_money_man_yen": None,
                "layout": "",
                "size_m2": _parse_m2(size_raw),
                "size_raw": size_raw,
                "floor": "",
                "floor_num": None,
                "building_age": None,
                "building_age_raw": "",
                "address": features,
                "transportation": transportation,
                "url": url,
                "image_url": img_url,
                "price_per_tsubo": price_per_tsubo,
                "building_coverage_ratio": building_coverage_ratio,
                "floor_area_ratio": floor_area_ratio,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.debug("Failed to parse sale item: %s", e)
            return None

    def _get_next_page_url(self) -> str | None:
        try:
            next_btn = self.page.query_selector("p.pagination-parts a:has-text('次へ')")
            if not next_btn:
                next_btn = self.page.query_selector("p.pagination-parts a.pagination-next")
            if next_btn:
                href = next_btn.get_attribute("href")
                if href and href.startswith("/"):
                    return "https://suumo.jp" + href
                return href
        except Exception:
            pass
        return None


def _parse_man_yen(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*万円", text)
    if match:
        return float(match.group(1))
    return None

def _parse_m2(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*(?:m|㎡)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None
