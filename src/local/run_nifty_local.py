"""
src/local/run_nifty_local.py
============================
Standalone runner để scrape myhome.nifty.com từ máy PC thật (residential IP).

Lý do tồn tại:
  - VPS bị Akamai WAF chặn do dải IP Datacenter.
  - Máy local có IP dân cư, vượt qua WAF tự nhiên.
  - Script này chạy độc lập, KHÔNG ảnh hưởng đến production code trên VPS.

Tính năng:
  - Tự đọc config.yaml (cùng config với production)
  - Chỉ chạy các search có site = "nifty"
  - Lưu seen_listings riêng tại src/local/seen_listings.json
    (Tách biệt hoàn toàn với results/seen_listings/ của VPS)
  - Gửi Telegram giống hệt production (filter, geocode, format)
  - Chạy headless=False theo mặc định để dùng browser thật

Cách chạy (từ thư mục gốc My-Home-Hunter):
  python -m src.local.run_nifty_local
  python -m src.local.run_nifty_local --headless     # chạy ẩn browser
  python -m src.local.run_nifty_local --search "Nifty Toyonaka Rental"

Lưu ý:
  - File seen_listings.json local sẽ tích lũy theo thời gian.
    Nếu muốn reset (scrape lại toàn bộ và thông báo tất cả), xóa file đó.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

# ── Ensure project root is on the path khi chạy trực tiếp ──────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.config import load_config, AppConfig, SearchConfig
from src.filter import ListingFilter
from src.notifier.telegram import TelegramNotifier
from src.geocoder import GeocoderService

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("local.nifty")

# ── Constants ────────────────────────────────────────────────────────────────
LOCAL_DIR = os.path.dirname(__file__)
LOCAL_SEEN_FILE = os.path.join(LOCAL_DIR, "seen_listings.json")


# ── Seen-listings helpers ────────────────────────────────────────────────────

def _load_local_seen() -> dict:
    """Load the local seen_listings store (separate from VPS store)."""
    if not os.path.exists(LOCAL_SEEN_FILE):
        return {}
    try:
        with open(LOCAL_SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("[Local] Loaded %d seen listings from local store.", len(data))
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("[Local] seen_listings.json corrupted or unreadable: %s — resetting.", e)
        return {}


def _save_local_seen(seen: dict) -> None:
    """Persist the local seen_listings store."""
    try:
        os.makedirs(LOCAL_DIR, exist_ok=True)
        with open(LOCAL_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
        logger.debug("[Local] Saved %d seen listings to local store.", len(seen))
    except IOError as e:
        logger.error("[Local] Failed to save seen_listings.json: %s", e)


# ── Core runner ──────────────────────────────────────────────────────────────

def run_nifty_search(
    search: SearchConfig,
    config: AppConfig,
    seen: dict,
    listing_filter: ListingFilter,
    telegram: TelegramNotifier,
    geocoder: GeocoderService,
    headless: bool,
) -> dict:
    """
    Scrape one Nifty search, filter results, geocode, notify Telegram.
    Returns the updated `seen` dict.
    """
    logger.info("=" * 60)
    logger.info("Starting local Nifty search: [%s]", search.name)
    logger.info("=" * 60)

    # ── Patch headless for local run ────────────────────────────────────────
    # We temporarily override general.headless to run with a real browser
    original_headless = config.general.headless
    config.general.headless = headless

    try:
        from src.scraper.nifty_rental_hunter import NiftyRentalHunter
        hunter = NiftyRentalHunter(search=search, general=config.general)

        # Run scraper (returns all_listings, but we handle seen ourselves)
        all_listings = hunter.scrape()

    except Exception as e:
        logger.error("[%s] Scraping failed: %s", search.name, e, exc_info=True)
        if "WAF_BLOCK" in str(e):
            msg = (
                f"🚨 *CẢNH BÁO: LOCAL BỊ CHẶN BỞI WAF (NIFTY)*\n"
                f"Search: `{search.name}`\n"
                f"Nifty phát hiện bot ngay cả trên máy local. "
                f"Thử mở Nifty trong Chrome thật trước, rồi chạy lại."
            )
            telegram.send_text(msg)
        all_listings = []
    finally:
        config.general.headless = original_headless

    # ── Deduplicate against local seen store ─────────────────────────────────
    new_listings = [l for l in all_listings if l.get("url") and l["url"] not in seen]

    logger.info(
        "[%s] Result: %d total | %d new",
        search.name, len(all_listings), len(new_listings),
    )

    # ── Geocode all (for filter + Telegram map link) ─────────────────────────
    loc_cfg = config.filters.location_filter
    for listing in all_listings:
        address = listing.get("address", "")
        if address:
            lat, lng = geocoder.get_coordinates(address)
            listing["lat"] = lat
            listing["lng"] = lng
            if lat is not None and lng is not None and loc_cfg.enabled:
                dist = geocoder.calculate_distance(lat, lng, loc_cfg.center_lat, loc_cfg.center_lng)
                listing["distance_km"] = dist
            else:
                listing["distance_km"] = None
        else:
            listing["lat"] = None
            listing["lng"] = None
            listing["distance_km"] = None

    # ── Filter new listings ──────────────────────────────────────────────────
    matched = []
    for listing in new_listings:
        if not listing_filter.matches(listing):
            continue
        if loc_cfg.enabled:
            dist = listing.get("distance_km")
            if dist is None or dist > loc_cfg.max_distance_km:
                logger.debug(
                    "FILTERED distance %.1f > max %.1f [%s]",
                    dist or 999, loc_cfg.max_distance_km, listing.get("url"),
                )
                continue
        matched.append(listing)
        logger.info(
            "[%s] MATCHED: %s (Dist: %s) - %s",
            search.name,
            listing.get("name"),
            f"{listing.get('distance_km', 0):.2f} km" if listing.get("distance_km") is not None else "N/A",
            listing.get("url"),
        )

    logger.info(
        "[%s] %d new | %d matched filter → sending Telegram",
        search.name, len(new_listings), len(matched),
    )

    # ── Notify ──────────────────────────────────────────────────────────────
    if matched:
        telegram.send_batch(matched, search_name=f"[LOCAL] {search.name}")
    else:
        logger.info("[%s] No matching new listings to notify.", search.name)

    # ── Persist seen ─────────────────────────────────────────────────────────
    for listing in new_listings:
        url = listing.get("url")
        if url:
            seen[url] = listing

    return seen


def run_all(config: AppConfig, target_name: str | None, headless: bool) -> None:
    """Find all enabled Nifty searches and run them."""
    nifty_searches = [
        s for s in config.searches
        if s.enabled
        and s.site == "nifty"
        and (target_name is None or s.name == target_name)
    ]

    if not nifty_searches:
        logger.warning(
            "No enabled Nifty searches found%s.",
            f" matching '{target_name}'" if target_name else "",
        )
        return

    listing_filter = ListingFilter(config.filters)
    telegram = TelegramNotifier(config.notifications.telegram)
    geocoder = GeocoderService()
    seen = _load_local_seen()

    logger.info("Running %d Nifty search(es) locally (headless=%s)", len(nifty_searches), headless)

    for i, search in enumerate(nifty_searches):
        seen = run_nifty_search(
            search=search,
            config=config,
            seen=seen,
            listing_filter=listing_filter,
            telegram=telegram,
            geocoder=geocoder,
            headless=headless,
        )
        _save_local_seen(seen)

        if i < len(nifty_searches) - 1:
            delay = config.general.delay_between_searches
            if delay > 0:
                logger.info("Sleeping %ds before next search...", delay)
                time.sleep(delay)

    logger.info("All local Nifty searches completed. Total seen (cumulative): %d", len(seen))


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Local Nifty runner — scrapes Nifty from residential PC IP, sends Telegram."
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser in headless mode (hidden). Default: show browser window.",
    )
    parser.add_argument(
        "--search", metavar="NAME",
        help="Run only the Nifty search with this name (must match config.yaml exactly).",
    )
    args = parser.parse_args()

    # Load the shared config.yaml from project root
    config_path = os.path.join(_PROJECT_ROOT, "config.yaml")
    config = load_config(config_path)

    print("\n" + "=" * 60)
    print("  HOME-HUNTER — LOCAL NIFTY RUNNER")
    print("=" * 60)
    nifty_enabled = [s for s in config.searches if s.enabled and s.site == "nifty"]
    print(f"  Nifty searches : {len(nifty_enabled)}")
    for s in nifty_enabled:
        print(f"    - {s.name}")
    print(f"  Headless       : {args.headless}")
    print(f"  Local seen DB  : {LOCAL_SEEN_FILE}")
    tg = config.notifications.telegram
    print(f"  Telegram       : {'ON' if tg.enabled else 'OFF'}")
    print("=" * 60 + "\n")

    run_all(config, target_name=args.search, headless=args.headless)
    logger.info("Done.")


if __name__ == "__main__":
    main()
