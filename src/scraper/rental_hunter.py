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

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.scraper.base import AbstractHunter, WebDriverBase
from src.config import GeneralConfig, SearchConfig

logger = logging.getLogger(__name__)


class SUUMORentalHunter(AbstractHunter, WebDriverBase):
    """Scrapes rental listings from SUUMO chintai pages."""

    def __init__(self, search: SearchConfig, general: GeneralConfig):
        AbstractHunter.__init__(self, search_name=search.name)
        WebDriverBase.__init__(
            self,
            webdriver_path=general.webdriver_path,
            headless=general.headless,
            disable_images_css=general.disable_images_css,
        )
        self.start_url = search.url
        self.page_load_timeout = general.page_load_timeout
        self.delay_between_pages = general.delay_between_pages
        self.max_pages = general.max_pages_per_search

    # ------------------------------------------------------------------
    # AbstractHunter interface
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scrape_page(self, url: str, page_num: int) -> list[dict]:
        self.driver.get(url)

        # Wait for listings container
        try:
            WebDriverWait(self.driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".cassetteitem"))
            )
        except Exception:
            logger.warning("[%s] Page %d: timeout waiting for listings.", self.search_name, page_num)
            return []

        listings = []
        buildings = self.driver.find_elements(By.CSS_SELECTOR, ".cassetteitem")

        for building in buildings:
            listings.extend(self._parse_building(building))

        return listings

    def _parse_building(self, building) -> list[dict]:
        results = []
        try:
            building_name = building.find_element(By.CSS_SELECTOR, ".cassetteitem_content-title").text.strip()
            address = building.find_element(By.CSS_SELECTOR, ".cassetteitem_detail-col1").text.strip()
            transportation = building.find_element(By.CSS_SELECTOR, ".cassetteitem_detail-col2").text.strip()
        except NoSuchElementException:
            return results

        rows = building.find_elements(By.CSS_SELECTOR, "tr.js-cassette_link")
        for row in rows:
            listing = self._parse_room_row(row, building_name, address, transportation)
            if listing:
                results.append(listing)

        return results

    def _parse_room_row(self, row, building_name: str, address: str, transportation: str) -> dict | None:
        try:
            floor_raw = row.find_element(By.CSS_SELECTOR, "td:nth-child(3)").text.strip()
            rent_raw = row.find_element(By.CSS_SELECTOR, ".cassetteitem_other-emphasis").text.strip()
            admin_fee_raw = row.find_element(By.CSS_SELECTOR, ".cassetteitem_price--administration").text.strip()
            deposit_raw = row.find_element(By.CSS_SELECTOR, ".cassetteitem_price--deposit").text.strip()
            key_money_raw = row.find_element(By.CSS_SELECTOR, ".cassetteitem_price--gratuity").text.strip()
            layout = row.find_element(By.CSS_SELECTOR, ".cassetteitem_madori").text.strip()
            size_raw = row.find_element(By.CSS_SELECTOR, ".cassetteitem_menseki").text.strip()
            url = row.find_element(By.CSS_SELECTOR, "a.cassetteitem_other-linktext").get_attribute("href")

            # Try to get building age
            try:
                age_text = row.find_element(By.CSS_SELECTOR, ".cassetteitem_detail-col3 div").text.strip()
            except NoSuchElementException:
                age_text = ""

            # Try to get image
            try:
                image_url = building_element_image(row)
            except Exception:
                image_url = None

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
            next_btn = self.driver.find_elements(
                By.XPATH, "//p[@class='pagination-parts']/a[contains(text(), '次へ')]"
            )
            if next_btn:
                return next_btn[0].get_attribute("href")
        except Exception:
            pass
        return None


# ------------------------------------------------------------------
# Parse helpers
# ------------------------------------------------------------------

def building_element_image(row) -> str | None:
    """Try to get the thumbnail image from the building card."""
    try:
        img = row.find_element(By.CSS_SELECTOR, ".casssetteitem_other-thumbnail-img")
        return img.get_attribute("src")
    except Exception:
        return None


def _parse_man_yen(text: str) -> float | None:
    """Parse '7.5万円' -> 7.5. Returns None if not parseable."""
    if not text:
        return None
    # Handle cases like "-" or "なし"
    match = re.search(r"([\d.]+)\s*万円", text)
    if match:
        return float(match.group(1))
    return None


def _parse_yen(text: str) -> float | None:
    """Parse '5,000円' -> 5000. Returns None if not parseable."""
    if not text:
        return None
    match = re.search(r"([\d,]+)\s*円", text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def _parse_m2(text: str) -> float | None:
    """Parse '42.5m2' or '42.5㎡' -> 42.5."""
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*(?:m|㎡)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _parse_floor_num(text: str) -> int | None:
    """Parse '3階' -> 3."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*階", text)
    if match:
        return int(match.group(1))
    return None


def _parse_building_age(text: str) -> int | None:
    """Parse '築29年' -> 29. '新築' -> 0."""
    if not text:
        return None
    if "新築" in text:
        return 0
    match = re.search(r"築(\d+)年", text)
    if match:
        return int(match.group(1))
    return None
