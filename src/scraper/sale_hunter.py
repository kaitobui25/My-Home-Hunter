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

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from src.scraper.base import AbstractHunter, WebDriverBase
from src.config import GeneralConfig, SearchConfig

logger = logging.getLogger(__name__)


class SUUMOSaleHunter(AbstractHunter, WebDriverBase):
    """Scrapes sale/land listings from SUUMO bukken pages."""

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scrape_page(self, url: str, page_num: int) -> list[dict]:
        self.driver.get(url)
        try:
            WebDriverWait(self.driver, self.page_load_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#right_sliderList2"))
            )
        except Exception:
            logger.warning("[%s] Page %d: timeout.", self.search_name, page_num)
            return []

        listings = []
        items = self.driver.find_elements(
            By.CSS_SELECTOR,
            "#right_sliderList2 li[id^='jsiRightSliderListChild_']",
        )

        for item in items:
            listing = self._parse_item(item)
            if listing:
                listings.append(listing)

        return listings

    def _parse_item(self, item) -> dict | None:
        try:
            property_name = item.find_element(By.CSS_SELECTOR, "p a").text.strip()
            url = item.find_element(By.CSS_SELECTOR, "p a").get_attribute("href")

            # Price
            price_raw = "Not found"
            price_per_tsubo = "Not found"
            price_elements = item.find_elements(
                By.XPATH, ".//div[@class='fr w105 bw']/p[contains(text(), '円')]"
            )
            for elem in price_elements:
                text = elem.text
                if "万円" in text and price_raw == "Not found":
                    price_raw = text.strip()
                elif "坪単価" in text:
                    m = re.search(r"[\d.]+万円", text)
                    if m:
                        price_per_tsubo = m.group()

            # Size
            try:
                size_raw = item.find_element(By.CSS_SELECTOR, "div.fr p:nth-of-type(2)").text
                size_raw = size_raw.replace("土地／", "").replace("㎡", "m2").strip()
                size_raw = re.sub(r"<[^>]+>", "", size_raw)
            except NoSuchElementException:
                size_raw = "Not Available"

            # Building coverage / floor area ratios
            try:
                ratios_elem = item.find_element(
                    By.XPATH,
                    ".//div[@class='fr w105 bw']/p[contains(text(), '建ぺい率・容積率')]",
                )
                _, combined = ratios_elem.text.split("／")
                bcr, far = combined.split("\u3000")  # fullwidth space
                building_coverage_ratio = bcr.strip()
                floor_area_ratio = far.strip()
            except (NoSuchElementException, ValueError):
                building_coverage_ratio = "N/A"
                floor_area_ratio = "N/A"

            # Features
            feature_elems = item.find_elements(By.CSS_SELECTOR, "ul.cf li")
            features = "\n".join(e.text for e in feature_elems)

            # Transportation
            try:
                transportation = item.find_element(By.CSS_SELECTOR, "p.mt5:nth-of-type(2)").text.strip()
            except NoSuchElementException:
                transportation = "N/A"

            # Image
            try:
                img_url = item.find_element(By.CSS_SELECTOR, ".fl.w90 img").get_attribute("src")
                img_url = re.sub(r"&w=\d+&h=\d+", "&w=500&h=500", img_url)
            except NoSuchElementException:
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
                "layout": "",          # Sale listings typically don't have layout
                "size_m2": _parse_m2(size_raw),
                "size_raw": size_raw,
                "floor": "",
                "floor_num": None,
                "building_age": None,  # Sale pages don't expose this directly
                "building_age_raw": "",
                "address": features,  # features text includes address for sale
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
            # Sale pages use different pagination
            next_btn = self.driver.find_elements(
                By.XPATH, "//p[@class='pagination-parts']/a[contains(text(), '次へ')]"
            )
            if next_btn:
                return next_btn[0].get_attribute("href")
            # Also try common SUUMO next-page button variants
            next_btn = self.driver.find_elements(
                By.CSS_SELECTOR, "p.pagination-parts a.pagination-next"
            )
            if next_btn:
                return next_btn[0].get_attribute("href")
        except Exception:
            pass
        return None


# ------------------------------------------------------------------
# Parse helpers (shared with rental hunter via copy — small enough)
# ------------------------------------------------------------------

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
