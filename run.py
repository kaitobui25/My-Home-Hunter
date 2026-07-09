"""
run.py — Home-Hunter main entry point.

Usage:
    python run.py                  # Run once then wait (loop mode)
    python run.py --once           # Run once and exit
    python run.py --search "Name"  # Run only a specific search by name

All configuration is read from config.yaml.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time

from src.config import load_config, AppConfig, SearchConfig
from src.filter import ListingFilter
from src.exporter.csv_exporter import CSVExporter
from src.notifier.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    logger.warning("Signal received, shutting down gracefully...")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as compact human-readable time."""
    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes, remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remaining_seconds:.1f}s"

    hours, remaining_minutes = divmod(int(minutes), 60)
    return f"{hours}h {remaining_minutes:02d}m {remaining_seconds:.1f}s"


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def create_hunter(search: SearchConfig, config: AppConfig):
    """Factory: return the right hunter for the search type and site."""
    site = search.site  # auto-detected from URL in config.py

    if site == "nifty":
        if search.type == "rental":
            from src.scraper.nifty_rental_hunter import NiftyRentalHunter
            return NiftyRentalHunter(search=search, general=config.general)
        else:
            raise ValueError(f"Nifty site only supports 'rental' type, got: '{search.type}'.")
    elif site == "homes":
        if search.type == "rental":
            from src.scraper.homes_rental_hunter import HOMESRentalHunter
            return HOMESRentalHunter(search=search, general=config.general, filters=config.filters)
        else:
            raise ValueError(f"HOMES site only supports 'rental' type, got: '{search.type}'.")

    # Default: SUUMO
    if search.type == "rental":
        from src.scraper.rental_hunter import SUUMORentalHunter
        return SUUMORentalHunter(search=search, general=config.general)
    elif search.type == "sale":
        from src.scraper.sale_hunter import SUUMOSaleHunter
        return SUUMOSaleHunter(search=search, general=config.general)
    else:
        raise ValueError(f"Unknown search type: '{search.type}'. Use 'rental' or 'sale'.")


from src.geocoder import GeocoderService

def run_search(search: SearchConfig, config: AppConfig,
               listing_filter: ListingFilter,
               csv_exporter: CSVExporter,
               telegram: TelegramNotifier,
               geocoder: GeocoderService):
    """Run a single search: scrape → filter → export → notify."""
    search_started_at = time.perf_counter()
    logger.info("=" * 60)
    logger.info("Starting search: [%s] (%s)", search.name, search.type)
    logger.info("[%s] Search started", search.name)
    logger.info("=" * 60)

    hunter = create_hunter(search, config)
    all_listings, new_listings = hunter.run()
    logger.info(
        "[%s] Scrape completed in %s",
        search.name,
        _format_elapsed(time.perf_counter() - search_started_at),
    )

    # Geocode and calculate distance for ALL listings (so CSV has them)
    loc_cfg = config.filters.location_filter
    for listing in all_listings:
        address = listing.get("address", "")
        if address:
            lat, lng = geocoder.get_coordinates(address)
            listing["lat"] = lat
            listing["lng"] = lng
            
            if lat is not None and lng is not None and loc_cfg.enabled:
                dist = geocoder.calculate_distance(
                    lat, lng, loc_cfg.center_lat, loc_cfg.center_lng
                )
                listing["distance_km"] = dist
            else:
                listing["distance_km"] = None
        else:
            listing["lat"] = None
            listing["lng"] = None
            listing["distance_km"] = None

    # Filter new listings for notification
    matched = []
    for l in new_listings:
        if not listing_filter.matches(l):
            continue
            
        # Location filter
        if loc_cfg.enabled:
            dist = l.get("distance_km")
            if dist is None or dist > loc_cfg.max_distance_km:
                logger.debug("FILTERED distance %.1f > max %.1f [%s]", dist or 999, loc_cfg.max_distance_km, l.get("url"))
                continue
                
        matched.append(l)
        logger.info("[%s] MATCHED: %s (Dist: %.2f km) - %s", search.name, l.get("name"), l.get("distance_km", 0), l.get("url"))

    logger.info(
        "[%s] Result: %d total | %d new | %d matched filter",
        search.name, len(all_listings), len(new_listings), len(matched),
    )

    # Export ALL scraped listings to CSV
    csv_path = csv_exporter.write(all_listings, search.name)
    if csv_path:
        logger.info("[%s] CSV saved: %s", search.name, csv_path)

    # Notify only matched new listings
    telegram_active = bool(
        telegram.cfg.enabled and telegram.cfg.bot_token and telegram.cfg.chat_id
    )
    telegram_ok = True
    if matched:
        if telegram_active:
            sent_count = telegram.send_batch(matched, search_name=search.name)
            if sent_count == 0:
                telegram_ok = False
                logger.warning(
                    "[%s] Telegram notifications failed; will not mark new listings as seen.",
                    search.name,
                )
        else:
            logger.info("[%s] Telegram disabled or missing configuration; skipping notifications.", search.name)
    else:
        logger.info("[%s] No matching new listings to notify.", search.name)

    if not matched or not telegram_active:
        hunter.mark_seen(new_listings)
    else:
        if sent_count > 0:
            hunter.mark_seen(matched[:sent_count])
        nonmatched = [l for l in new_listings if l not in matched]
        if nonmatched:
            hunter.mark_seen(nonmatched)
        if sent_count < len(matched):
            logger.warning(
                "[%s] %d/%d matched listings were not sent and will retry in the next run.",
                search.name, len(matched) - sent_count, len(matched),
            )

    elapsed = time.perf_counter() - search_started_at
    summary = {
        "search_name": search.name,
        "elapsed_sec": round(elapsed, 1),
        "total": len(all_listings),
        "new": len(new_listings),
        "matched": len(matched),
    }
    logger.info(
        "[%s] Finished in %s | total=%d | new=%d | matched=%d",
        search.name,
        _format_elapsed(elapsed),
        summary["total"],
        summary["new"],
        summary["matched"],
    )
    logger.info(
        "HH_SEARCH_SUMMARY %s",
        json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
    )
    return summary


def run_all(config: AppConfig, target_name: str | None = None):
    """Run all enabled searches (or just one if target_name is set)."""
    run_started_at = time.perf_counter()
    success_count = 0
    failed_count = 0

    listing_filter = ListingFilter(config.filters)
    csv_exporter = CSVExporter(config.export.csv)
    telegram = TelegramNotifier(config.notifications.telegram)
    geocoder = GeocoderService()

    searches_to_run = [
        s for s in config.searches
        if s.enabled and (target_name is None or s.name == target_name)
    ]

    if not searches_to_run:
        logger.warning("No enabled searches found%s.",
                       f" matching '{target_name}'" if target_name else "")
        elapsed = time.perf_counter() - run_started_at
        logger.info(
            "Run completed in %s | searches=0 | success=0 | failed=0",
            _format_elapsed(elapsed),
        )
        logger.info(
            "HH_RUN_SUMMARY %s",
            json.dumps(
                {
                    "elapsed_sec": round(elapsed, 1),
                    "searches": 0,
                    "success": 0,
                    "failed": 0,
                },
                separators=(",", ":"),
            ),
        )
        return

    for i, search in enumerate(searches_to_run):
        if _shutdown:
            break
        search_started_at = time.perf_counter()
        try:
            run_search(search, config, listing_filter, csv_exporter, telegram, geocoder)
        except Exception as e:
            failed_count += 1
            elapsed = time.perf_counter() - search_started_at
            logger.info(
                "[%s] Failed in %s | total=0 | new=0 | matched=0",
                search.name,
                _format_elapsed(elapsed),
            )
            logger.info(
                "HH_SEARCH_SUMMARY %s",
                json.dumps(
                    {
                        "search_name": search.name,
                        "elapsed_sec": round(elapsed, 1),
                        "total": 0,
                        "new": 0,
                        "matched": 0,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            logger.error("Search '%s' failed: %s", search.name, e, exc_info=True)
            if "WAF_BLOCK" in str(e):
                msg = (
                    f"🚨 *CẢNH BÁO: BỊ CHẶN BỞI WAF (NIFTY)*\n"
                    f"Search: `{search.name}`\n"
                    f"Cookie đã hết hạn hoặc IP bị chặn. Vui lòng lấy lại Cookie mới ở máy cá nhân và đè vào file `nifty_cookies.json` trên VPS!"
                )
                telegram.send_text(msg)
        else:
            success_count += 1
            
        if i < len(searches_to_run) - 1 and not _shutdown:
            delay = config.general.delay_between_searches
            if delay > 0:
                logger.info("Sleeping %d seconds before next search to free up RAM...", delay)
                _sleep_interruptible(delay)

    elapsed = time.perf_counter() - run_started_at
    logger.info(
        "Run completed in %s | searches=%d | success=%d | failed=%d",
        _format_elapsed(elapsed),
        len(searches_to_run),
        success_count,
        failed_count,
    )
    logger.info(
        "HH_RUN_SUMMARY %s",
        json.dumps(
            {
                "elapsed_sec": round(elapsed, 1),
                "searches": len(searches_to_run),
                "success": success_count,
                "failed": failed_count,
            },
            separators=(",", ":"),
        ),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Home-Hunter: SUUMO listing monitor"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run once and exit (default: loop mode)",
    )
    parser.add_argument(
        "--search", metavar="NAME",
        help="Run only the search with this name (must match config exactly)",
    )
    args = parser.parse_args()

    config = load_config("config.yaml")

    _print_banner(config)

    if args.once:
        run_all(config, target_name=args.search)
        logger.info("Done (--once mode). Exiting.")
        return

    # Loop mode
    logger.info(
        "Loop mode: checking every %d seconds. Press Ctrl+C to stop.",
        config.general.check_interval_seconds,
    )
    while not _shutdown:
        run_all(config, target_name=args.search)
        if _shutdown:
            break
        logger.info(
            "Sleeping %d seconds until next check...",
            config.general.check_interval_seconds,
        )
        _sleep_interruptible(config.general.check_interval_seconds)

    logger.info("Home-Hunter stopped.")


def _sleep_interruptible(seconds: int):
    """Sleep in small chunks so Ctrl+C is responsive."""
    for _ in range(seconds):
        if _shutdown:
            break
        time.sleep(1)


def _print_banner(config: AppConfig):
    enabled = [s for s in config.searches if s.enabled]
    print("\n" + "=" * 60)
    print("  HOME-HUNTER")
    print("=" * 60)
    print(f"  Searches enabled : {len(enabled)}")
    for s in enabled:
        print(f"    - [{s.type.upper()}] {s.name}")
    print(f"  CSV export       : {'ON' if config.export.csv.enabled else 'OFF'}")
    tg = config.notifications.telegram
    print(f"  Telegram notify  : {'ON' if tg.enabled else 'OFF (set enabled: true in config)'}")
    print(f"  Check interval   : {config.general.check_interval_seconds}s")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
