"""
src/scraper/homes_rental_hunter.py
Scrapes HOMES (homes.co.jp) rental listing pages.
Supports URL parameter injection for efficient scraping.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from src.scraper.base import AbstractHunter, PlaywrightBase
from src.config import GeneralConfig, SearchConfig

logger = logging.getLogger(__name__)

class HOMESRentalHunter(AbstractHunter, PlaywrightBase):
    """Scrapes rental listings from HOMES."""

    MADORI_MAP = {
        "1R": "1",
        "1K": "5",
        "1DK": "10",
        "1LDK": "15",
        "2K": "20",
        "2DK": "25",
        "2LDK": "30",
        "3K": "35",
        "3DK": "40",
        "3LDK": "45",
        "4K": "50",
        "4DK": "55",
        "4LDK": "60",
    }

    def __init__(self, search: SearchConfig, general: GeneralConfig, filters=None):
        AbstractHunter.__init__(self, search_name=search.name)
        PlaywrightBase.__init__(
            self,
            webdriver_path=general.webdriver_path,
            headless=general.headless,
            disable_images_css=general.disable_images_css,
        )
        self.start_url = self._inject_filters(search.url, filters)
        # Lưu filter params để merge vào mọi next-page URL
        self._filter_params = parse_qs(urlparse(self.start_url).query)
        self.page_load_timeout = general.page_load_timeout * 1000
        self.delay_between_pages = general.delay_between_pages
        self.max_pages = general.max_pages_per_search

    def _init_driver(self):
        """Override to add stealth UA and remove webdriver fingerprint for HOMES WAF."""
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        
        # HYBRID APPROACH: Inject cookies from local machine to bypass WAF
        import os, json
        cookie_path = os.path.join(os.getcwd(), "homes_cookies.json")
        if os.path.exists(cookie_path):
            try:
                with open(cookie_path, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                self.context.add_cookies(cookies)
                logger.info("[%s] Successfully injected %d cookies from homes_cookies.json", self.search_name, len(cookies))
            except Exception as e:
                logger.warning("[%s] Failed to load homes_cookies.json: %s", self.search_name, e)

        self.page = self.context.new_page()

        if self.disable_images_css:
            self.page.route("**/*", self._block_resources)

    def _inject_filters(self, url: str, filters) -> str:
        """Inject filters into the URL using HOMES-specific cond[] parameters."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        # Remove any generic params already in URL (they don't work on HOMES)
        for k in ["pr_min", "pr_max", "ts_min", "madori"]:
            params.pop(k, None)

        if filters and hasattr(filters, "rental") and filters.rental:
            r = filters.rental

            # 1. Rent: HOMES dùng cond[monthmoneyroom] (min) và cond[monthmoneyroomh] (max)
            if getattr(r, "min_rent_man_yen", None):
                params["cond[monthmoneyroom]"] = [str(r.min_rent_man_yen)]
            if getattr(r, "max_rent_man_yen", None):
                params["cond[monthmoneyroomh]"] = [str(r.max_rent_man_yen)]

            # 2. Area: HOMES dùng cond[housearea] (min) — lấy từ filters cấp cao
            min_size = getattr(r, "min_size_m2", None) or getattr(filters, "min_size_m2", None)
            if min_size:
                params["cond[housearea]"] = [str(int(min_size))]

            # 3. Layout: HOMES dùng cond[madori][XX] = XX
            # Ánh xạ từ code Nifty-style → HOMES code
            # Nifty: 1R=10,1K=11,1DK=13,1LDK=15,2K=20,2DK=23,2LDK=25,3K=30,3DK=33,3LDK=35,4K=40,4DK=43,4LDK=45
            # HOMES: 1R=11,1K=12,1DK=13,1LDK=15,2K=22,2DK=23,2LDK=25,3K=32,3DK=33,3LDK=35,4K=42,4DK=43,4LDK=45
            nifty_to_homes = {
                "10": "11", "11": "12", "13": "13", "15": "15",
                "20": "22", "23": "23", "25": "25",
                "30": "32", "33": "33", "35": "35",
                "40": "42", "43": "43", "45": "45",
            }
            layout_name_to_homes = {
                "2K": "22", "2DK": "23", "2LDK": "25",
                "3K": "32", "3DK": "33", "3LDK": "35",
                "4K": "42", "4DK": "43", "4LDK": "45",
            }
            madori_raw = getattr(r, "madori", None) or getattr(r, "allowed_layouts", None)
            if madori_raw:
                if isinstance(madori_raw, str):
                    madori_raw = [m.strip() for m in madori_raw.split(",")]
                for m in madori_raw:
                    m = str(m).strip()
                    hcode = nifty_to_homes.get(m) or layout_name_to_homes.get(m.upper())
                    if hcode:
                        params[f"cond[madori][{hcode}]"] = [hcode]

        new_query = urlencode(params, doseq=True)
        # HOMES expects raw brackets in params (e.g. cond[madori][22]=22), not URL-encoded form
        new_query = new_query.replace("%5B", "[").replace("%5D", "]")
        return urlunparse(parsed._replace(query=new_query))

    def scrape(self) -> list[dict]:
        all_listings = []
        current_url = self.start_url
        page_num = 1

        try:
            while current_url and page_num <= self.max_pages:
                logger.info("[%s] Scraping HOMES page %d: %s", self.search_name, page_num, current_url)
                page_listings = self._scrape_page(current_url, page_num)
                all_listings.extend(page_listings)
                logger.info(
                    "[%s] Page %d: %d units found (total so far: %d)",
                    self.search_name, page_num, len(page_listings), len(all_listings),
                )

                current_url = self._get_next_page_url()
                if current_url:
                    page_num += 1
                    time.sleep(self.delay_between_pages)
        except Exception as e:
            logger.error("[%s] HOMES Scraping failed: %s", self.search_name, e, exc_info=True)
        finally:
            self.close_driver()

        return all_listings

    def _scrape_page(self, url: str, page_num: int) -> list[dict]:
        try:
            self.page.goto(url, timeout=self.page_load_timeout, wait_until="domcontentloaded")
        except Exception as e:
            logger.warning("[%s] Page %d goto failed: %s", self.search_name, page_num, e)
            return []

        # Wait for listings
        try:
            self.page.wait_for_selector(".prg-building, .cassette-unit", timeout=self.page_load_timeout)
        except Exception:
            logger.warning("[%s] Page %d: timeout waiting for selector. Saving dump to homes_error.html", self.search_name, page_num)
            try:
                with open("homes_error.html", "w", encoding="utf-8") as f:
                    f.write(self.page.content())
            except Exception as e:
                logger.error("Could not save homes_error.html: %s", e)
            return []

        listings = []
        buildings = self.page.query_selector_all(".prg-building, .cassette-unit")
        for building in buildings:
            listings.extend(self._parse_building(building))
        return listings

    def _parse_building(self, building) -> list[dict]:
        results = []
        try:
            # HOMES Name selector
            name_elem = building.query_selector(".prg-bukkenNameAnchor .bukkenName, .cassette-unit-title a, .prg-bukkenNameAnchor")
            building_name = name_elem.inner_text().strip() if name_elem else "Unknown Building"
            
            # Details: Address, Transportation, Age
            address = ""
            transportation = ""
            age_text = ""
            
            # Try new table structure
            trs = building.query_selector_all("table tr")
            for tr in trs:
                th = tr.query_selector("th")
                td = tr.query_selector("td")
                if not th or not td: continue
                label = th.inner_text().strip()
                val = td.inner_text().strip()
                if "所在地" in label: address = val.replace("\n", " ")
                elif "交通" in label: transportation = val.replace("\n", " ")
                elif "築年数" in label or "築年月" in label: age_text = val

            # Fallback to old dl/dt/dd structure
            if not address:
                details = building.query_selector_all(".cassette-unit-detail dl")
                for dl in details:
                    dt = dl.query_selector("dt")
                    dd = dl.query_selector("dd")
                    if not dt or not dd: continue
                    label = dt.inner_text().strip()
                    val = dd.inner_text().strip()
                    if "所在地" in label: address = val
                    elif "交通" in label: transportation = val
                    elif "築年月" in label: age_text = val

            # Units table
            # Try new structure
            rows = building.query_selector_all("tbody.prg-roomList tr.prg-roomInfo")
            if not rows:
                # Try old structure
                rows = building.query_selector_all(".cassette-unit-table tbody tr")
                
            for row in rows:
                if "PR" in (row.get_attribute("class") or ""): continue
                
                listing = self._parse_room_row(row, building_name, address, transportation, age_text)
                if listing:
                    results.append(listing)
        except Exception as e:
            logger.debug("Error parsing building: %s", e)
        return results

    def _parse_room_row(self, row, building_name: str, address: str, transportation: str, age_text: str) -> dict | None:
        try:
            # Try new structure first
            rent_elem = row.query_selector("td.price")
            layout_elem = row.query_selector("td.layout")
            floor_elem = row.query_selector("td.floar .roomKaisuu, td.floar")
            url_elem = row.query_selector("td.detail a.prg-detailAnchor")

            # Fallback to old structure
            if not rent_elem:
                rent_elem = row.query_selector(".cassette-unit-table-price span")
            if not layout_elem:
                layout_elem = row.query_selector(".cassette-unit-table-madori")
                size_elem = row.query_selector(".cassette-unit-table-menseki")
            if not floor_elem:
                floor_elem = row.query_selector(".cassette-unit-table-floor")
            if not url_elem:
                url_elem = row.query_selector(".cassette-unit-table-detail a")

            rent_raw = rent_elem.inner_text().strip() if rent_elem else ""
            
            # In new structure, layout_elem contains both layout and size separated by newline
            layout_raw = layout_elem.inner_text().strip() if layout_elem else ""
            layout = layout_raw.split("\n")[0].strip() if "\n" in layout_raw else layout_raw
            size_raw = layout_raw.split("\n")[1].strip() if "\n" in layout_raw else layout_raw
            
            floor_raw = floor_elem.inner_text().strip() if floor_elem else ""
            url = url_elem.get_attribute("href") if url_elem else ""

            if url and url.startswith("/"):
                url = "https://www.homes.co.jp" + url

            return {
                "name": building_name,
                "listing_type": "rental",
                "price_raw": rent_raw,
                "price_man_yen": _parse_man_yen(rent_raw),
                "admin_fee_raw": rent_raw, # Handled by parse_yen from rent_raw
                "admin_fee_yen": _parse_yen(rent_raw),
                "deposit_raw": rent_raw, # Deposit is usually underneath in the same cell in new structure
                "deposit_man_yen": None,
                "key_money_raw": rent_raw,
                "key_money_man_yen": None,
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
                "image_url": None,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception:
            return None

    def _get_next_page_url(self) -> str | None:
        try:
            next_btn = self.page.query_selector("li.nextPage a, li.next a")
            if next_btn:
                href = next_btn.get_attribute("href")
                if not href:
                    return None
                if href.startswith("/"):
                    href = "https://www.homes.co.jp" + href
                # Merge filter params vào next-page URL (giữ cond[] filters qua mọi trang)
                parsed = urlparse(href)
                page_params = parse_qs(parsed.query)
                # Merge: filter params được ưu tiên, page param giữ lại
                merged = {**self._filter_params, **{k: v for k, v in page_params.items() if k == "page"}}
                new_query = urlencode(merged, doseq=True)
                new_query = new_query.replace("%5B", "[").replace("%5D", "]")
                return urlunparse(parsed._replace(query=new_query))
        except Exception:
            pass
        return None

# Helpers (Redefined for modularity)
def _parse_man_yen(text: str) -> float | None:
    if not text: return None
    match = re.search(r"([\d.]+)\s*万円", text)
    return float(match.group(1)) if match else None

def _parse_yen(text: str) -> float | None:
    if not text: return None
    match = re.search(r"([\d,]+)\s*円", text)
    return float(match.group(1).replace(",", "")) if match else None

def _parse_m2(text: str) -> float | None:
    if not text: return None
    match = re.search(r"([\d.]+)\s*(?:m|㎡)", text, re.IGNORECASE)
    return float(match.group(1)) if match else None

def _parse_floor_num(text: str) -> int | None:
    if not text: return None
    match = re.search(r"(\d+)\s*階", text)
    return int(match.group(1)) if match else None

def _parse_building_age(text: str) -> int | None:
    if not text: return None
    if "新築" in text: return 0
    # HOMES uses 築年月 like "1990年10月"
    match = re.search(r"(\d{4})年", text)
    if match:
        year = int(match.group(1))
        current_year = datetime.now().year
        return max(0, current_year - year)
    return None
