"""Progressive local runner for Home Hunter.

Key differences from ``src.local.run_local``:
- Nifty is processed one page at a time and persisted after every page, so the
  map can display matching homes while the remaining pages continue.
- Existing parser/filter/geocoder/notifier code is reused rather than forked.
- Playwright hunters use safer cleanup and shorter page pacing.
- The recurring faulthandler dump is disabled because its ``Timeout`` banner
  looked like a real failure even when the scraper was healthy.
"""
from __future__ import annotations

import argparse
import copy
import faulthandler
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from src.config import AppConfig, SearchConfig, load_config
from src.filter import ListingFilter
from src.geocoder import GeocoderService
from src.notifier.telegram import TelegramNotifier
from src.scraper.fast_rental_hunters import (
    FastHOMESRentalHunter,
    FastNiftyRentalHunter,
)
from src.local import run_local as legacy
from src.local.yahoo_integration import run_yahoo_search

logger = logging.getLogger("local.runner.v2")

try:
    faulthandler.cancel_dump_traceback_later()
except Exception:
    pass

import src.scraper.homes_rental_hunter as homes_module
import src.scraper.nifty_rental_hunter as nifty_module

homes_module.HOMESRentalHunter = FastHOMESRentalHunter
nifty_module.NiftyRentalHunter = FastNiftyRentalHunter

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _nifty_page_url(url: str, page_number: int) -> str:
    """Return a stable Nifty pagination URL without disturbing query filters."""
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if segments and segments[-1].isdigit():
        segments.pop()
    path = "/" + "/".join(segments) + "/"
    if page_number > 1:
        path += f"{page_number}/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _telegram_is_active(telegram: TelegramNotifier) -> bool:
    cfg = telegram.cfg
    return bool(cfg.enabled and cfg.bot_token and cfg.chat_id)


def _run_nifty_progressive(
    search: SearchConfig,
    config: AppConfig,
    seen: dict,
    listing_filter: ListingFilter,
    telegram: TelegramNotifier,
    geocoder: GeocoderService,
    headless: bool,
) -> tuple[dict, set[str], bool]:
    """Scrape/filter/save one Nifty page at a time."""
    max_pages = max(1, int(config.general.max_pages_per_search))
    page_config = copy.copy(config)
    page_config.general = copy.copy(config.general)
    page_config.general.max_pages_per_search = 1
    page_config.general.delay_between_pages = 0.0

    all_canonical: set[str] = set()
    completed_all_requested_pages = True
    original_telegram_enabled = telegram.cfg.enabled
    notify_after_scan = _telegram_is_active(telegram)

    # Avoid retrying every old tele_sent=False filtered entry on every page.
    # Send once after all pages using the existing refilter pipeline.
    if notify_after_scan:
        telegram.cfg.enabled = False

    try:
        for page_number in range(1, max_pages + 1):
            page_search = copy.copy(search)
            page_search.url = _nifty_page_url(search.url, page_number)

            legacy._emit_phase(
                "scraping_page",
                search_name=search.name,
                message=f"Nifty page {page_number}/{max_pages}",
                page=page_number,
                max_pages=max_pages,
            )
            page_started = time.perf_counter()
            seen, canonical = legacy.run_local_search(
                search=page_search,
                config=page_config,
                seen=seen,
                listing_filter=listing_filter,
                telegram=telegram,
                geocoder=geocoder,
                headless=headless,
                on_seen_updated=legacy._save_local_seen,
            )
            elapsed = time.perf_counter() - page_started

            if not canonical:
                logger.info(
                    "[%s] Page %d returned no listing URLs; stopping pagination.",
                    search.name,
                    page_number,
                )
                completed_all_requested_pages = False
                break

            all_canonical.update(canonical)
            legacy._save_local_seen(seen)
            legacy._emit_progress(
                page_done=True,
                search_name=search.name,
                page=page_number,
                max_pages=max_pages,
                page_seconds=round(elapsed, 1),
                scraped=len(all_canonical),
            )
    finally:
        telegram.cfg.enabled = original_telegram_enabled

    if notify_after_scan:
        logger.info("[%s] Progressive scan complete; running one notification pass.", search.name)
        seen = legacy.run_refilter(
            config=config,
            target_name=search.name,
            seen=seen,
            listing_filter=listing_filter,
            telegram=telegram,
            geocoder=geocoder,
        )
        legacy._save_local_seen(seen)

    return seen, all_canonical, completed_all_requested_pages


def _url_site(url: str) -> str | None:
    if "realestate.yahoo.co.jp" in url:
        return "yahoo"
    if "nifty.com" in url:
        return "nifty"
    if "homes.co.jp" in url:
        return "homes"
    if "suumo.jp" in url:
        return "suumo"
    return None


def _apply_delist_pass(seen: dict, site_canonical: dict[str, set[str]]) -> None:
    if not site_canonical:
        return
    legacy._emit_phase("delist_pass", message="Checking for delisted listings")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    count = 0
    for url, entry in seen.items():
        site = _url_site(url)
        if not site or site not in site_canonical:
            continue
        if legacy._canonical_url(url) in site_canonical[site]:
            if entry.get("delisted"):
                entry["delisted"] = False
                entry["delisted_at"] = None
        elif not entry.get("delisted"):
            entry["delisted"] = True
            entry["delisted_at"] = now_iso
            count += 1
    legacy._emit_phase(
        "delist_done",
        message=f"Delist pass complete: {count} listings marked as gone",
        delisted=count,
    )
    legacy._save_local_seen(seen)


def run_all(config: AppConfig, target_name: str | None, headless: bool) -> None:
    active_searches = [
        search
        for search in config.searches
        if search.enabled and (target_name is None or search.name == target_name)
    ]
    if not active_searches:
        logger.warning(
            "No enabled searches found%s.",
            f" matching '{target_name}'" if target_name else "",
        )
        return

    listing_filter = ListingFilter(config.filters)
    telegram = TelegramNotifier(config.notifications.telegram)
    geocoder = GeocoderService()
    seen = legacy._load_local_seen()
    site_canonical: dict[str, set[str]] = {}

    logger.info(
        "Running %d search(es) with progressive runner (headless=%s)",
        len(active_searches),
        headless,
    )
    legacy._emit_progress(run_start=True, total=len(active_searches))
    run_started = time.perf_counter()

    for index, search in enumerate(active_searches, start=1):
        search_started = time.perf_counter()
        legacy._emit_progress(
            search_start=True,
            search_name=search.name,
            site=search.site or "suumo",
            index=index,
            total=len(active_searches),
        )
        legacy._emit_phase(
            "scraping",
            search_name=search.name,
            message=f"Scraping {search.site or 'suumo'}",
        )

        if search.site == "nifty" and search.type == "rental":
            seen, canonical, complete_for_delist = _run_nifty_progressive(
                search,
                config,
                seen,
                listing_filter,
                telegram,
                geocoder,
                headless,
            )
        elif search.site == "yahoo" and search.type == "rental":
            seen, canonical = run_yahoo_search(
                search=search,
                config=config,
                seen=seen,
                listing_filter=listing_filter,
                telegram=telegram,
                geocoder=geocoder,
                on_seen_updated=legacy._save_local_seen,
            )
            complete_for_delist = bool(canonical)
        else:
            seen, canonical = legacy.run_local_search(
                search=search,
                config=config,
                seen=seen,
                listing_filter=listing_filter,
                telegram=telegram,
                geocoder=geocoder,
                headless=headless,
                on_seen_updated=legacy._save_local_seen,
            )
            complete_for_delist = bool(canonical)

        if canonical and complete_for_delist:
            site_canonical.setdefault(search.site or "suumo", set()).update(canonical)
        elif canonical:
            logger.warning(
                "[%s] Partial scrape detected; skipping delist pass for safety.",
                search.name,
            )

        elapsed = time.perf_counter() - search_started
        legacy._emit_phase(
            "search_done",
            search_name=search.name,
            message=f"Search completed in {elapsed:.0f}s",
            seconds=round(elapsed, 1),
            scraped=len(canonical),
        )
        legacy._emit_progress(
            search_done=True,
            search_name=search.name,
            site=search.site or "suumo",
            index=index,
            total=len(active_searches),
            seconds=round(elapsed, 1),
        )
        legacy._save_local_seen(seen)

        if index < len(active_searches):
            delay = max(0.0, float(config.general.delay_between_searches))
            if delay:
                logger.info("Sleeping %.1fs before next search...", delay)
                time.sleep(delay)

    total_elapsed = time.perf_counter() - run_started
    legacy._emit_progress(run_done=True, total_seconds=round(total_elapsed, 1))
    _apply_delist_pass(seen, site_canonical)
    legacy._emit_phase(
        "run_done",
        message=f"All done in {total_elapsed:.0f}s",
        total_seconds=round(total_elapsed, 1),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Progressive local Home Hunter runner")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--search", metavar="NAME")
    parser.add_argument("--reset-tele", action="store_true")
    parser.add_argument("--refilter", action="store_true")
    args = parser.parse_args()

    config = load_config(os.path.join(_PROJECT_ROOT, "config.yaml"))

    print("\n" + "=" * 60)
    print("  HOME-HUNTER - PROGRESSIVE LOCAL RUNNER")
    print("=" * 60)
    print(f"  Active searches: {sum(1 for s in config.searches if s.enabled)}")
    print(f"  Headless       : {args.headless}")
    print(f"  Local seen DB  : {legacy.LOCAL_SEEN_FILE}")
    print("  Nifty mode     : page-by-page, save/display immediately")
    print("  Yahoo mode     : direct HTTP, no browser")
    print("=" * 60 + "\n")

    if args.reset_tele or args.refilter:
        seen = legacy._load_local_seen()
        if args.reset_tele:
            seen = legacy.reset_tele_sent(
                seen,
                search_names=[args.search] if args.search else None,
            )
            legacy._save_local_seen(seen)
        if args.refilter:
            seen = legacy.run_refilter(
                config=config,
                target_name=args.search,
                seen=seen,
                listing_filter=ListingFilter(config.filters),
                telegram=TelegramNotifier(config.notifications.telegram),
                geocoder=GeocoderService(),
            )
            legacy._save_local_seen(seen)
        return

    run_all(config, target_name=args.search, headless=args.headless)


if __name__ == "__main__":
    main()
