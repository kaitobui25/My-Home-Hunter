"""Faster and safer Playwright-backed rental hunters.

The site-specific schemas stay compatible with the original hunters, while
expensive per-element Playwright round trips are collapsed into one DOM
extraction call per Nifty result page.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import src.scraper.nifty_rental_hunter as nifty_module
from src.scraper.homes_rental_hunter import HOMESRentalHunter
from src.scraper.nifty_rental_hunter import NiftyRentalHunter

logger = logging.getLogger(__name__)

_NIFTY_PAGE_EXTRACTOR = r"""
(cards) => cards.flatMap((card) => {
    const text = (node) => (node?.textContent || "").trim();
    const buildingName = text(card.querySelector("a.text.is-middle.is-strong.is-sm"));

    const address = Array.from(
        card.querySelectorAll("p.text.is-line-height-sm.is-sm")
    ).map(text).find((value) => value && /[府県都道市区町丁]/.test(value)) || "";

    const transportation = Array.from(
        card.querySelectorAll("li[data-transport-access]")
    ).map(text).filter(Boolean).join(" | ");

    let buildingAgeRaw = "";
    for (const dl of card.querySelectorAll(".bukken-info-items dl")) {
        if (text(dl.querySelector("dt")).includes("築年数")) {
            buildingAgeRaw = text(dl.querySelector("dd"));
            break;
        }
    }

    return Array.from(card.querySelectorAll("tbody.click-area"))
        .map((tbody) => {
            const firstTr = tbody.querySelector("tr");
            if (!firstTr) return null;

            let floorRaw = "";
            let layout = "";
            let sizeRaw = "";
            let rentRaw = "";
            let adminFeeRaw = "";
            let depositRaw = "";
            let keyMoneyRaw = "";

            const rentPs = tbody.querySelectorAll("td.bukken-info-rent p");
            if (rentPs.length > 0) rentRaw = text(rentPs[0]);
            if (rentPs.length > 1) adminFeeRaw = text(rentPs[1]);

            const detailLink = tbody.querySelector("a[href*='/detail_']");
            const url = detailLink?.getAttribute("href") || "";

            for (const td of firstTr.querySelectorAll("td[data-link-wrap-item]")) {
                if (td.getAttribute("rowspan")) continue;
                const ps = td.querySelectorAll("p");
                if (!ps.length) continue;
                const text0 = text(ps[0]);
                const text1 = ps.length > 1 ? text(ps[1]) : "";

                if (!floorRaw && text0.includes("階")) {
                    floorRaw = text0;
                } else if (
                    !layout &&
                    (text0.includes("LDK") || text0.includes("DK") || /^\d+(?:R|K|DK|LDK)(?:\+S)?$/.test(text0))
                ) {
                    layout = text0;
                    sizeRaw = text1;
                }
            }

            for (const dl of tbody.querySelectorAll("dl")) {
                const label = text(dl.querySelector("dt"));
                const value = text(dl.querySelector("dd"));
                if (label === "敷" || label === "敷金") depositRaw = value;
                if (label === "礼" || label === "礼金") keyMoneyRaw = value;
            }

            if (!rentRaw && !layout && !url) return null;
            return {
                buildingName,
                address,
                transportation,
                buildingAgeRaw,
                floorRaw,
                layout,
                sizeRaw,
                rentRaw,
                adminFeeRaw,
                depositRaw,
                keyMoneyRaw,
                url,
            };
        })
        .filter(Boolean);
})
"""

_NIFTY_NEXT_LINK_EXTRACTOR = r"""
(links, nextPage) => {
    const expected = String(nextPage);
    for (const link of links) {
        const href = link.getAttribute("href") || "";
        const label = (link.textContent || "").trim();
        if (
            label === expected ||
            label.includes("次へ") ||
            label === ">" ||
            href.includes(`/${expected}/?`)
        ) {
            return href;
        }
    }
    return null;
}
"""


def _fast_page_delay(current: float) -> float:
    """Cap per-page sleep while still allowing a safer override via env."""
    raw = os.environ.get("HOME_HUNTER_PAGE_DELAY_SECONDS", "0.35")
    try:
        configured = max(0.0, float(raw))
    except (TypeError, ValueError):
        configured = 0.35
    return min(max(0.0, float(current)), configured)


class SafePlaywrightCloseMixin:
    """Close every Playwright layer independently.

    The original implementation wrapped all cleanup in one try block. If the
    first close raised, the Node driver could survive and later write to a pipe
    that Python had already closed, producing a secondary EPIPE traceback.
    """

    def close_driver(self) -> None:
        for attr, method_name in (
            ("page", "close"),
            ("context", "close"),
            ("browser", "close"),
            ("playwright", "stop"),
        ):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                getattr(obj, method_name)()
            except Exception as exc:  # cleanup must continue through all layers
                logger.debug("Playwright cleanup failed at %s.%s: %s", attr, method_name, exc)
            finally:
                try:
                    setattr(self, attr, None)
                except Exception:
                    pass


class FastNiftyRentalHunter(SafePlaywrightCloseMixin, NiftyRentalHunter):
    """Nifty scraper that extracts each page in one browser round trip."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_between_pages = _fast_page_delay(self.delay_between_pages)
        try:
            self.page.set_default_timeout(min(self.page_load_timeout, 20_000))
        except Exception:
            pass

    def _normalize_room(self, raw: dict) -> dict:
        rent_raw = raw.get("rentRaw", "")
        admin_fee_raw = raw.get("adminFeeRaw", "")
        deposit_raw = raw.get("depositRaw", "")
        key_money_raw = raw.get("keyMoneyRaw", "")
        size_raw = raw.get("sizeRaw", "")
        floor_raw = raw.get("floorRaw", "")
        age_raw = raw.get("buildingAgeRaw", "")
        return {
            "name": raw.get("buildingName", ""),
            "listing_type": "rental",
            "price_raw": rent_raw,
            "price_man_yen": nifty_module._parse_man_yen(rent_raw),
            "admin_fee_raw": admin_fee_raw,
            "admin_fee_yen": nifty_module._parse_yen(admin_fee_raw),
            "deposit_raw": deposit_raw,
            "deposit_man_yen": nifty_module._parse_man_yen_or_yen(deposit_raw),
            "key_money_raw": key_money_raw,
            "key_money_man_yen": nifty_module._parse_man_yen_or_yen(key_money_raw),
            "layout": raw.get("layout", ""),
            "size_m2": nifty_module._parse_m2(size_raw),
            "size_raw": size_raw,
            "floor": floor_raw,
            "floor_num": nifty_module._parse_floor_num(floor_raw),
            "building_age": nifty_module._parse_building_age(age_raw),
            "building_age_raw": age_raw,
            "address": raw.get("address", ""),
            "transportation": raw.get("transportation", ""),
            "url": nifty_module._make_absolute(raw.get("url", "")),
            "image_url": None,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

    def _extract_current_page(self) -> list[dict]:
        raw_rooms = self.page.eval_on_selector_all(
            "li.result-bukken-list",
            _NIFTY_PAGE_EXTRACTOR,
        )
        return [self._normalize_room(room) for room in raw_rooms]

    def _next_page_url(self, page_num: int) -> str | None:
        try:
            href = self.page.eval_on_selector_all(
                ".pager a, .pagination a, a.button.is-outline.is-link-done",
                _NIFTY_NEXT_LINK_EXTRACTOR,
                page_num + 1,
            )
        except Exception as exc:
            logger.debug("[%s] Failed to find next page link: %s", self.search_name, exc)
            return None
        return nifty_module._make_absolute(href) if href else None

    def scrape(self) -> list[dict]:
        all_listings: list[dict] = []
        current_url = self.start_url
        page_num = 1

        try:
            while current_url and page_num <= self.max_pages:
                logger.info(
                    "[%s] Scraping page %d: %s",
                    self.search_name,
                    page_num,
                    current_url,
                )
                try:
                    self.page.goto(
                        current_url,
                        timeout=self.page_load_timeout,
                        wait_until="domcontentloaded",
                    )
                    self.page.wait_for_selector(
                        "li.result-bukken-list",
                        timeout=self.page_load_timeout,
                    )
                except Exception as exc:
                    if page_num == 1 and "Timeout" in str(exc):
                        raise RuntimeError(
                            "WAF_BLOCK: Nifty timed out. The IP may be tar-pitted or cookies expired."
                        ) from exc
                    logger.warning(
                        "[%s] Page %d load failed: %s",
                        self.search_name,
                        page_num,
                        exc,
                    )
                    break

                page_started = time.perf_counter()
                page_listings = self._extract_current_page()
                all_listings.extend(page_listings)
                logger.info(
                    "[%s] Page %d: extracted %d rooms in %.2fs (total=%d)",
                    self.search_name,
                    page_num,
                    len(page_listings),
                    time.perf_counter() - page_started,
                    len(all_listings),
                )

                if page_num >= self.max_pages:
                    break
                current_url = self._next_page_url(page_num)
                if not current_url:
                    logger.info(
                        "[%s] No next page found after page %d.",
                        self.search_name,
                        page_num,
                    )
                    break
                page_num += 1
                if self.delay_between_pages:
                    time.sleep(self.delay_between_pages)
        except Exception as exc:
            logger.error("[%s] Scraping failed: %s", self.search_name, exc, exc_info=True)
            if "WAF_BLOCK" in str(exc):
                raise
        finally:
            self.close_driver()

        return all_listings


class FastHOMESRentalHunter(SafePlaywrightCloseMixin, HOMESRentalHunter):
    """HOME'S parser with safe cleanup and shorter pacing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delay_between_pages = _fast_page_delay(self.delay_between_pages)
        try:
            self.page.set_default_timeout(min(self.page_load_timeout, 20_000))
        except Exception:
            pass
