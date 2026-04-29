"""
src/scraper/nifty_rental_hunter.py
Scrapes myhome.nifty.com rental listing pages.

DOM structure (verified 2026-04-29 from live page):
  - Each building card: li.result-bukken-list > div.card
  - Building name: a.text.is-middle.is-strong.is-sm  (href = /mansion-info/...)
  - Address: p.text.is-line-height-sm.is-sm  (inside div[has svg map-marker])
  - Transportation: li[data-transport-access]
  - Building age: .bukken-info-items dl dt='築年数' -> dd value e.g. '40年'
  - Room rows: tbody.click-area (each building may have multiple)
  - Per room tbody tr:
      col3 td p: floor (e.g. '1階')
      col4 td p[0]: layout (e.g. '2DK')
      col4 td p[1]: size (e.g. '29.16㎡')
      td.bukken-info-rent p[0]: rent (e.g. '4.5万円' rendered as span+text)
      td.bukken-info-rent p[1]: admin fee (e.g. '4,000円')
      last td div dl dt='敷' -> dd: deposit
      last td div dl dt='礼' -> dd: key money
  - Detail URL: a[href*='/detail_'] inside tbody
  - Pagination: infinite scroll — we scroll to bottom to load all cards.

NOTE: Nifty returns ~36 buildings per page and uses infinite scroll.
We scroll in a loop until no new content appears.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from src.scraper.base import AbstractHunter, PlaywrightBase
from src.config import GeneralConfig, SearchConfig

logger = logging.getLogger(__name__)

NIFTY_BASE = "https://myhome.nifty.com"


class NiftyRentalHunter(AbstractHunter, PlaywrightBase):
    """Scrapes rental listings from myhome.nifty.com."""

    def __init__(self, search: SearchConfig, general: GeneralConfig):
        AbstractHunter.__init__(self, search_name=search.name)
        PlaywrightBase.__init__(
            self,
            webdriver_path=general.webdriver_path,
            headless=general.headless,
            disable_images_css=general.disable_images_css,
        )
        self.start_url = search.url
        self.page_load_timeout = general.page_load_timeout * 1000  # ms
        self.delay_between_pages = general.delay_between_pages
        self.max_pages = general.max_pages_per_search

    # ------------------------------------------------------------------
    # PlaywrightBase override: use stealth context for Nifty
    # ------------------------------------------------------------------

    def _init_driver(self):
        """Override to add stealth UA and remove webdriver fingerprint."""
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
        # Remove webdriver fingerprint
        self.context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        
        # HYBRID APPROACH: Inject cookies from local machine to bypass WAF
        import os, json
        cookie_path = os.path.join(os.getcwd(), "nifty_cookies.json")
        if os.path.exists(cookie_path):
            try:
                with open(cookie_path, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                self.context.add_cookies(cookies)
                logger.info("[%s] Successfully injected %d cookies from nifty_cookies.json", self.search_name, len(cookies))
            except Exception as e:
                logger.warning("[%s] Failed to load nifty_cookies.json: %s", self.search_name, e)

        self.page = self.context.new_page()

        if self.disable_images_css:
            self.page.route("**/*", self._block_resources)

    # ------------------------------------------------------------------
    # Main scrape: load page → scroll to bottom → parse all cards
    # ------------------------------------------------------------------

    def scrape(self) -> list[dict]:
        all_listings: list[dict] = []

        try:
            logger.info("[%s] Navigating to %s", self.search_name, self.start_url)
            try:
                self.page.goto(self.start_url, timeout=self.page_load_timeout, wait_until="domcontentloaded")
            except Exception as e:
                if "Timeout" in str(e):
                    raise Exception("WAF_BLOCK: Lỗi Timeout khi truy cập Nifty. Có thể IP đã bị Tarpit hoặc Cookie hết hạn. Vui lòng cập nhật lại nifty_cookies.json!") from e
                raise

            # Wait for initial listings to appear
            try:
                self.page.wait_for_selector("li.result-bukken-list", timeout=self.page_load_timeout)
            except Exception as e:
                raise Exception("WAF_BLOCK: Nifty chặn truy cập (WAF/Captcha). Cookie có thể đã hết hạn. Vui lòng lấy lại Cookie mới và cập nhật nifty_cookies.json!") from e

            # Pagination loop: process current page, then find next page link
            page_num = 1
            current_url = self.start_url

            while current_url and page_num <= self.max_pages:
                logger.info(
                    "[%s] Scraping page %d: %s", self.search_name, page_num, current_url
                )
                try:
                    if page_num > 1:
                        self.page.goto(current_url, timeout=self.page_load_timeout, wait_until="domcontentloaded")
                except Exception as e:
                    logger.warning("[%s] Page %d goto failed: %s", self.search_name, page_num, e)
                    break

                # Wait for initial listings to appear
                try:
                    self.page.wait_for_selector("li.result-bukken-list", timeout=self.page_load_timeout)
                except Exception:
                    logger.warning("[%s] Timeout waiting for listings on page %d.", self.search_name, page_num)
                    break

                # Parse all loaded cards on this page
                cards = self.page.query_selector_all("li.result-bukken-list")
                logger.info("[%s] Page %d: Parsing %d building cards...", self.search_name, page_num, len(cards))

                for card in cards:
                    all_listings.extend(self._parse_building_card(card))

                # Check if we should stop
                if page_num >= self.max_pages:
                    break

                # Find next page URL
                next_url = None
                try:
                    # Nifty pagination has next arrow icon, typically last pagination link
                    # Or look for any link containing the next page number e.g. /2/
                    next_links = self.page.query_selector_all(".pager a, .pagination a, a.button.is-outline.is-link-done")
                    for link in next_links:
                        href = link.get_attribute("href")
                        text = link.inner_text().strip()
                        # If the text is the next page number, or if it's the > arrow (next)
                        if str(page_num + 1) == text or ">" in text or "次へ" in text:
                            next_url = _make_absolute(href)
                            break
                        
                        # Fallback: check if the href contains /<page_num+1>/?
                        if href and f"/{page_num + 1}/?" in href:
                            next_url = _make_absolute(href)
                            break
                except Exception as e:
                    logger.debug("[%s] Failed to find next page link: %s", self.search_name, e)

                if next_url:
                    current_url = next_url
                    page_num += 1
                    time.sleep(self.delay_between_pages)
                else:
                    logger.info("[%s] No next page found after page %d. Reached end.", self.search_name, page_num)
                    break

        except Exception as e:
            logger.error("[%s] Scraping failed: %s", self.search_name, e, exc_info=True)
        finally:
            self.close_driver()

        return all_listings

    # ------------------------------------------------------------------
    # Building card parser
    # ------------------------------------------------------------------

    def _parse_building_card(self, card) -> list[dict]:
        results: list[dict] = []

        # ---- Building-level fields ----
        building_name = ""
        try:
            name_elem = card.query_selector("a.text.is-middle.is-strong.is-sm")
            if name_elem:
                building_name = name_elem.inner_text().strip()
        except Exception:
            pass

        # Address: p.text.is-line-height-sm.is-sm (the one inside a div with map-marker icon)
        # In practice: second div.box > div.box > p.text
        address = ""
        try:
            # All small paragraphs; the address contains prefecture kanji
            ps = card.query_selector_all("p.text.is-line-height-sm.is-sm")
            for p in ps:
                txt = p.inner_text().strip()
                if txt and any(k in txt for k in ["府", "県", "都", "道", "市", "区", "町", "丁"]):
                    address = txt
                    break
        except Exception:
            pass

        # Transportation: li[data-transport-access]
        transportation = ""
        try:
            trans_items = card.query_selector_all("li[data-transport-access]")
            parts = [li.inner_text().strip() for li in trans_items if li.inner_text().strip()]
            transportation = " | ".join(parts)
        except Exception:
            pass

        # Building age: .bukken-info-items dl where dt text contains "築年数"
        building_age_raw = ""
        try:
            dl_items = card.query_selector_all(".bukken-info-items dl")
            for dl in dl_items:
                dt_elem = dl.query_selector("dt")
                dd_elem = dl.query_selector("dd")
                if dt_elem and dd_elem:
                    dt_text = dt_elem.inner_text().strip()
                    if "築年数" in dt_text:
                        building_age_raw = dd_elem.inner_text().strip()  # e.g. "40年"
                        break
        except Exception:
            pass

        # ---- Room-level rows: each tbody.click-area is one available room ----
        try:
            tbodies = card.query_selector_all("tbody.click-area")
        except Exception:
            tbodies = []

        for tbody in tbodies:
            listing = self._parse_room_tbody(
                tbody,
                building_name=building_name,
                address=address,
                transportation=transportation,
                building_age_raw=building_age_raw,
            )
            if listing:
                results.append(listing)

        return results

    # ------------------------------------------------------------------
    # Room tbody parser
    # ------------------------------------------------------------------

    def _parse_room_tbody(
        self,
        tbody,
        building_name: str,
        address: str,
        transportation: str,
        building_age_raw: str,
    ) -> dict | None:
        try:
            # Get the first <tr> (main data row)
            first_tr = tbody.query_selector("tr")
            if not first_tr:
                return None

            all_tds = first_tr.query_selector_all("td[data-link-wrap-item]")
            # DOM structure of first tr:
            #   td[0] — photo (rowspan=2, skip)
            #   td[1] — floor plan image (rowspan=2, skip)
            #   td[2] — 階数 (floor)
            #   td[3] — 間取り / 専有面積 (layout + size)
            #   td[4] — 賃料 / 管理費等 (rent + admin fee) = bukken-info-rent
            #   td[5] — 敷金/礼金

            # The rowspan=2 TDs shift our indexing — use position-based approach
            # More reliable: get ALL tds in the row and identify by content/class

            floor_raw = ""
            layout = ""
            size_raw = ""
            rent_raw = ""
            admin_fee_raw = ""
            deposit_raw = ""
            key_money_raw = ""
            url = ""

            # Rent by class (most reliable)
            rent_td = tbody.query_selector("td.bukken-info-rent")
            if rent_td:
                rent_ps = rent_td.query_selector_all("p")
                if len(rent_ps) >= 1:
                    rent_raw = rent_ps[0].inner_text().strip()
                if len(rent_ps) >= 2:
                    admin_fee_raw = rent_ps[1].inner_text().strip()

            # Detail URL
            detail_link = tbody.query_selector("a[href*='/detail_']")
            if not detail_link:
                # Try the click-area parent's JS link via data-link-wrap (empty) — fallback to onclick
                pass
            if detail_link:
                href = detail_link.get_attribute("href")
                url = _make_absolute(href)

            # If no direct link found, try from the whole card — will be set later if needed

            # Floor, layout, size — from td[data-link-wrap-item] that are NOT rowspan
            for td in all_tds:
                rowspan = td.get_attribute("rowspan")
                if rowspan:  # skip rowspan=2 TDs (photo columns)
                    continue
                ps = td.query_selector_all("p")
                if not ps:
                    continue
                text0 = ps[0].inner_text().strip() if len(ps) > 0 else ""
                text1 = ps[1].inner_text().strip() if len(ps) > 1 else ""

                # Floor: contains 階
                if "階" in text0 and not floor_raw:
                    floor_raw = text0
                # Layout + size: text0 contains LDK/DK/K/R, text1 contains ㎡
                elif ("LDK" in text0 or "DK" in text0 or "DK" in text0
                      or re.search(r'\dR$|\dK$|\dDK$|\dLDK$|\dLDK\+S', text0)):
                    layout = text0
                    size_raw = text1

            # Deposit + key money
            dl_items = tbody.query_selector_all("dl")
            for dl in dl_items:
                dt_elem = dl.query_selector("dt")
                dd_elem = dl.query_selector("dd")
                if not (dt_elem and dd_elem):
                    continue
                dt_text = dt_elem.inner_text().strip()
                dd_text = dd_elem.inner_text().strip()
                if dt_text in ("敷", "敷金"):
                    deposit_raw = dd_text
                elif dt_text in ("礼", "礼金"):
                    key_money_raw = dd_text

            # Skip rows with no useful data
            if not rent_raw and not layout and not url:
                return None

            return {
                "name": building_name,
                "listing_type": "rental",
                "price_raw": rent_raw,
                "price_man_yen": _parse_man_yen(rent_raw),
                "admin_fee_raw": admin_fee_raw,
                "admin_fee_yen": _parse_yen(admin_fee_raw),
                "deposit_raw": deposit_raw,
                "deposit_man_yen": _parse_man_yen_or_yen(deposit_raw),
                "key_money_raw": key_money_raw,
                "key_money_man_yen": _parse_man_yen_or_yen(key_money_raw),
                "layout": layout,
                "size_m2": _parse_m2(size_raw),
                "size_raw": size_raw,
                "floor": floor_raw,
                "floor_num": _parse_floor_num(floor_raw),
                "building_age": _parse_building_age(building_age_raw),
                "building_age_raw": building_age_raw,
                "address": address,
                "transportation": transportation,
                "url": url,
                "image_url": None,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.debug("[%s] Failed to parse tbody: %s", building_name, e)
            return None


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def _make_absolute(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return NIFTY_BASE + href
    return href


def _parse_man_yen(text: str) -> float | None:
    """Extract 万円 value. '4.5万円' -> 4.5. '不要' -> 0.0."""
    if not text:
        return None
    if "不要" in text or "なし" in text:
        return 0.0
    match = re.search(r"([\d.]+)\s*万円", text)
    if match:
        return float(match.group(1))
    return None


def _parse_yen(text: str) -> float | None:
    """Extract 円 value. '4,000円' -> 4000.0. '不要' -> 0.0."""
    if not text:
        return None
    if "不要" in text or "なし" in text:
        return 0.0
    # First try 万円
    m = re.search(r"([\d.]+)\s*万円", text)
    if m:
        return float(m.group(1)) * 10000
    m = re.search(r"([\d,]+)\s*円", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _parse_man_yen_or_yen(text: str) -> float | None:
    """Deposit/key money can be expressed as 万円 or 円."""
    if not text:
        return None
    if "不要" in text or "なし" in text:
        return 0.0
    m = re.search(r"([\d.]+)\s*万円", text)
    if m:
        return float(m.group(1))
    # Convert 円 to 万円 for filter compatibility
    m = re.search(r"([\d,]+)\s*円", text)
    if m:
        return float(m.group(1).replace(",", "")) / 10000
    return None


def _parse_m2(text: str) -> float | None:
    """'29.16㎡' -> 29.16."""
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*(?:㎡|m2|m²)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _parse_floor_num(text: str) -> int | None:
    """'1階' -> 1. 'B1階' -> None (basement, skip)."""
    if not text:
        return None
    # Ignore basement
    if text.startswith("B") or "B" in text.upper()[:2]:
        return None
    match = re.search(r"(\d+)\s*階", text)
    if match:
        return int(match.group(1))
    return None


def _parse_building_age(text: str) -> int | None:
    """'40年' -> 40. '新築' -> 0."""
    if not text:
        return None
    if "新築" in text:
        return 0
    # Nifty format: "40年" (just number + 年, no 築 prefix in the dd value)
    match = re.search(r"(\d+)\s*年", text)
    if match:
        return int(match.group(1))
    return None
