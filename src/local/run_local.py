"""
src/local/run_local.py
============================
Standalone runner để scrape myhome.nifty.com từ máy PC thật (residential IP).

Lý do tồn tại:
  - VPS bị Akamai WAF chặn do dải IP Datacenter.
  - Máy local có IP dân cư, vượt qua WAF tự nhiên.
  - Script này chạy độc lập, KHÔNG ảnh hưởng đến production code trên VPS.

Tính năng:
  - Tự đọc config.yaml (cùng config với production)
  - Chỉ chạy các search có site = "nifty"
  - Lưu seen_listings tại results-local/local_seen_listings.json
    (git-tracked → sync qua nhiều PC khi commit/pull)
  - Gửi Telegram giống hệt production (filter, geocode, format)
  - Chạy headless=False theo mặc định để dùng browser thật
  - Dedup chắc chắn: tele_sent=True mới là "đã thông báo", tránh gửi trùng

Cách chạy (từ thư mục gốc My-Home-Hunter):
  python -m src.local.run_local                    # Scrape bình thường
  python -m src.local.run_local --headless          # Chạy ẩn browser
  python -m src.local.run_local --search "Name"     # Chỉ chạy 1 search
  python -m src.local.run_local --reset-tele        # Reset flag tele_sent để gửi lại
  python -m src.local.run_local --refilter          # Lọc lại data cũ, không mở browser
  python -m src.local.run_local --reset-tele --refilter  # Reset + gửi lại ngay

Chế độ đặc biệt:
  --reset-tele
    Reset tele_sent=False cho toàn bộ listing trong DB local.
    Dùng khi muốn gửi lại Telegram toàn bộ listing đã seen.
    Kết hợp --refilter để reset + gửi lại ngay trong 1 lần.

  --refilter
    Không mở browser. Dùng data đã lưu trong DB local, chạy lại toàn bộ
    pipeline filter + geocode + Telegram với config.yaml hiện tại.
    Dùng khi thay đổi config (ví dụ: max_distance_km) mà không muốn scrape lại.
    Chỉ gửi những listing chưa tele_sent (hoặc đã reset bởi --reset-tele).

Lưu ý:
  - results-local/local_seen_listings.json được commit vào git.
    Khi pull về PC2, script sẽ nhớ toàn bộ listing đã sent tele từ PC1.
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
logger = logging.getLogger("local.runner")

# ── Constants ──────────────────────────────────────────────────────────────────
LOCAL_DIR = os.path.dirname(__file__)
_PROJECT_ROOT_ALIAS = os.path.abspath(os.path.join(LOCAL_DIR, "..", ".."))

# Lưu ở results-local/ (git-tracked) — để sync qua nhiều PC bằng git
RESULTS_LOCAL_DIR = os.path.join(_PROJECT_ROOT_ALIAS, "results-local")
LOCAL_SEEN_FILE = os.path.join(RESULTS_LOCAL_DIR, "local_seen_listings.json")


# ── Seen-listings helpers ──────────────────────────────────────────────────────────────────

def _load_local_seen() -> dict:
    """
    Load seen listings store từ results-local/ (git-tracked).
    
    Cấu trúc mỗi entry:
    {
        "url": {
            "scraped_at": "ISO8601",
            "tele_sent": True/False,
            "tele_sent_at": "ISO8601" | null,
            ...listing_data
        }
    }
    
    Lưu ý: Chỉ những listing có tele_sent=True mới được coi là "đã xử lý xong".
    Listing có tele_sent=False là đã scrape nhưng chưa gửi được (ví dụ: bị filter).
    """
    os.makedirs(RESULTS_LOCAL_DIR, exist_ok=True)
    if not os.path.exists(LOCAL_SEEN_FILE):
        logger.info("[Local] No seen_listings file found. Starting fresh: %s", LOCAL_SEEN_FILE)
        return {}
    try:
        with open(LOCAL_SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        tele_sent_count = sum(1 for v in data.values() if v.get("tele_sent"))
        logger.info(
            "[Local] Loaded %d seen listings (%d sent to Telegram) from: %s",
            len(data), tele_sent_count, LOCAL_SEEN_FILE,
        )
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("[Local] seen_listings corrupted or unreadable: %s — resetting.", e)
        return {}


def _save_local_seen(seen: dict) -> None:
    """Persist the local seen_listings store vào results-local/."""
    try:
        os.makedirs(RESULTS_LOCAL_DIR, exist_ok=True)
        with open(LOCAL_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
        tele_sent_count = sum(1 for v in seen.values() if v.get("tele_sent"))
        logger.debug(
            "[Local] Saved %d seen listings (%d sent to Telegram) to: %s",
            len(seen), tele_sent_count, LOCAL_SEEN_FILE,
        )
    except IOError as e:
        logger.error("[Local] Failed to save seen_listings: %s", e)


def _mark_as_seen(seen: dict, listing: dict, tele_sent: bool) -> dict:
    """
    Đăng ký một listing vào seen store.
    - tele_sent=True: đã gửi Telegram thành công
    - tele_sent=False: đã scrape nhưng bị filter (không gửi)
    """
    url = listing.get("url")
    if not url:
        return seen
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    entry = {
        **{k: v for k, v in listing.items() if k not in ("lat", "lng", "distance_km")},
        "tele_sent": tele_sent,
        "tele_sent_at": now_iso if tele_sent else None,
        "first_seen_at": seen.get(url, {}).get("first_seen_at", now_iso),
    }
    seen[url] = entry
    return seen


# ── Core runner ──────────────────────────────────────────────────────────────

def run_local_search(
    search: SearchConfig,
    config: AppConfig,
    seen: dict,
    listing_filter: ListingFilter,
    telegram: TelegramNotifier,
    geocoder: GeocoderService,
    headless: bool,
) -> dict:
    """
    Scrape one search, filter results, geocode, notify Telegram.
    Returns the updated `seen` dict.
    """
    logger.info("=" * 60)
    logger.info("Starting local search: [%s]", search.name)
    logger.info("=" * 60)

    # ── Patch headless for local run ────────────────────────────────────────
    # We temporarily override general.headless to run with a real browser
    original_headless = config.general.headless
    config.general.headless = headless

    try:
        site = search.site  # auto-detected from URL in config.py
        if site == "nifty":
            if search.type == "rental":
                from src.scraper.nifty_rental_hunter import NiftyRentalHunter
                hunter = NiftyRentalHunter(search=search, general=config.general)
            else:
                raise ValueError(f"Nifty site only supports 'rental' type, got: '{search.type}'.")
        else:
            # Default: SUUMO
            if search.type == "rental":
                from src.scraper.rental_hunter import SUUMORentalHunter
                hunter = SUUMORentalHunter(search=search, general=config.general)
            elif search.type == "sale":
                from src.scraper.sale_hunter import SUUMOSaleHunter
                hunter = SUUMOSaleHunter(search=search, general=config.general)
            else:
                raise ValueError(f"Unknown search type: '{search.type}'. Use 'rental' or 'sale'.")

        # Run scraper (returns all_listings, but we handle seen ourselves)
        all_listings = hunter.scrape()

    except Exception as e:
        logger.error("[%s] Scraping failed: %s", search.name, e, exc_info=True)
        if "WAF_BLOCK" in str(e):
            msg = (
                f"🚨 *CẢNH BÁO: LOCAL BỊ CHẶN BỞI WAF ({search.site.upper() if search.site else 'UNKNOWN'})*\n"
                f"Search: `{search.name}`\n"
                f"Site phát hiện bot ngay cả trên máy local. "
                f"Thử mở web bằng trình duyệt thật trước, rồi chạy lại."
            )
            telegram.send_text(msg)
        all_listings = []
    finally:
        config.general.headless = original_headless

    # ── Deduplicate: chỉ xử lý listing chưa từng scrape được ──────────────────────────
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
            f"{listing.get('distance_km', 0):.2f} km" if listing.get('distance_km') is not None else "N/A",
            listing.get("url"),
        )

    logger.info(
        "[%s] %d new | %d matched filter → sending Telegram",
        search.name, len(new_listings), len(matched),
    )

    # ── Notify và Persist seen ─────────────────────────────────────────────────────────────
    if matched:
        telegram.send_batch(matched, search_name=f"[LOCAL] {search.name}")
        # Đánh dấu các listing đã gửi Telegram thành công
        for listing in matched:
            listing["search_name"] = search.name  # persist để --refilter dùng được
            _mark_as_seen(seen, listing, tele_sent=True)
    else:
        logger.info("[%s] No matching new listings to notify.", search.name)

    # Đánh dấu tất cả new listings (kể cả bị filter) là đã seen
    # → tránh scrape lại và gửi trùng vào lần sau
    for listing in new_listings:
        url = listing.get("url")
        if url and url not in seen:  # Chưa được đánh dấu bởi matched loop phía trên
            listing["search_name"] = search.name  # persist để --refilter dùng được
            _mark_as_seen(seen, listing, tele_sent=False)

    return seen


def reset_tele_sent(seen: dict, search_names: list | None = None) -> dict:
    """
    Reset tele_sent=False cho tất cả listing (hoặc listing thuộc các search cụ thể).
    Dùng khi muốn gửi lại Telegram lần nữa.
    """
    count = 0
    for entry in seen.values():
        # Nếu filter theo search_names thì chỉ reset listing của search đó
        if search_names is not None:
            if entry.get("search_name") not in search_names:
                continue
        if entry.get("tele_sent"):
            entry["tele_sent"] = False
            entry["tele_sent_at"] = None
            count += 1
    logger.info("[Reset] Reset tele_sent=False cho %d listing.", count)
    return seen


def run_refilter(
    config: AppConfig,
    target_name: str | None,
    seen: dict,
    listing_filter: ListingFilter,
    telegram: TelegramNotifier,
    geocoder: GeocoderService,
) -> dict:
    """
    Chạy lại filter + geocode + Telegram trên data đã lưu trong seen DB.
    KHÔNG mở browser. Chỉ xử lý listing có tele_sent=False.
    """
    from collections import defaultdict

    active_searches = [
        s for s in config.searches
        if s.enabled
        and (target_name is None or s.name == target_name)
    ]
    search_names_enabled = {s.name for s in active_searches}

    loc_cfg = config.filters.location_filter

    # Lấy tất cả listing chưa gửi tele (tele_sent=False) trong DB
    # Nếu search_name không có trong DB (data cũ), vẫn include vào để xử lý
    pending = [
        entry for entry in seen.values()
        if not entry.get("tele_sent")
        and (
            entry.get("search_name") in search_names_enabled
            or entry.get("search_name") is None  # data cũ chưa có search_name
        )
    ]

    logger.info(
        "[Refilter] Tìm thấy %d listing chưa gửi Telegram trong DB (thuộc %d search).",
        len(pending), len(active_searches),
    )

    if not pending:
        logger.info("[Refilter] Không có listing nào cần xử lý.")
        return seen

    # Geocode lại (dùng cache geocoder — không gọi API thêm nếu đã geocode trước)
    for listing in pending:
        address = listing.get("address", "")
        if address:
            lat, lng = geocoder.get_coordinates(address)
            listing["lat"] = lat
            listing["lng"] = lng
            if lat is not None and lng is not None and loc_cfg.enabled:
                listing["distance_km"] = geocoder.calculate_distance(
                    lat, lng, loc_cfg.center_lat, loc_cfg.center_lng
                )
            else:
                listing["distance_km"] = None
        else:
            listing["lat"] = None
            listing["lng"] = None
            listing["distance_km"] = None

    # Filter
    matched = []
    for listing in pending:
        if not listing_filter.matches(listing):
            continue
        if loc_cfg.enabled:
            dist = listing.get("distance_km")
            if dist is None or dist > loc_cfg.max_distance_km:
                logger.debug(
                    "[Refilter] FILTERED distance %.1f > max %.1f [%s]",
                    dist or 999, loc_cfg.max_distance_km, listing.get("url"),
                )
                continue
        matched.append(listing)
        logger.info(
            "[Refilter] MATCHED: %s (Dist: %s) - %s",
            listing.get("name"),
            f"{listing.get('distance_km', 0):.2f} km" if listing.get("distance_km") is not None else "N/A",
            listing.get("url"),
        )

    logger.info(
        "[Refilter] %d pending → %d matched → gửi Telegram",
        len(pending), len(matched),
    )

    if not matched:
        logger.info("[Refilter] Không có listing nào vượt filter.")
        return seen

    # Group theo search_name để gửi đúng nhãn
    groups: dict[str, list] = defaultdict(list)
    for listing in matched:
        groups[listing.get("search_name") or "Unknown"].append(listing)

    for sname, listings in groups.items():
        telegram.send_batch(listings, search_name=f"[LOCAL] {sname}")
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for listing in listings:
            url = listing.get("url")
            if url and url in seen:
                seen[url]["tele_sent"] = True
                seen[url]["tele_sent_at"] = now_iso

    return seen


def run_all(config: AppConfig, target_name: str | None, headless: bool) -> None:
    """Find all enabled searches and run them locally."""
    active_searches = [
        s for s in config.searches
        if s.enabled
        and (target_name is None or s.name == target_name)
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
    seen = _load_local_seen()

    logger.info("Running %d search(es) locally (headless=%s)", len(active_searches), headless)

    for i, search in enumerate(active_searches):
        seen = run_local_search(
            search=search,
            config=config,
            seen=seen,
            listing_filter=listing_filter,
            telegram=telegram,
            geocoder=geocoder,
            headless=headless,
        )
        _save_local_seen(seen)

        if i < len(active_searches) - 1:
            delay = config.general.delay_between_searches
            if delay > 0:
                logger.info("Sleeping %ds before next search...", delay)
                time.sleep(delay)

    logger.info("All local searches completed. Total seen (cumulative): %d", len(seen))


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Local runner — scrapes target sites from residential PC IP, sends Telegram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python -m src.local.run_local                        # Scrape bình thường
  python -m src.local.run_local --headless              # Chạy ẩn browser
  python -m src.local.run_local --reset-tele            # Reset DB rồi thoát
  python -m src.local.run_local --refilter              # Lọc lại data cũ, không mở browser
  python -m src.local.run_local --reset-tele --refilter # Reset + gửi lại ngay
        """,
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser in headless mode (hidden). Default: show browser window.",
    )
    parser.add_argument(
        "--search", metavar="NAME",
        help="Run only the Nifty search with this name (must match config.yaml exactly).",
    )
    parser.add_argument(
        "--reset-tele", action="store_true",
        help=(
            "Reset tele_sent=False cho tất cả listing trong DB local, "
            "để gửi lại Telegram lần nữa. "
            "Kết hợp với --refilter để gửi lại ngay không cần mở browser."
        ),
    )
    parser.add_argument(
        "--refilter", action="store_true",
        help=(
            "Không mở browser. Dùng data đã lưu trong DB local, "
            "chạy lại filter + geocode + Telegram với config hiện tại. "
            "Hữu ích khi thay đổi max_distance_km hoặc các filter khác."
        ),
    )
    args = parser.parse_args()

    # Load the shared config.yaml from project root
    config_path = os.path.join(_PROJECT_ROOT, "config.yaml")
    config = load_config(config_path)

    # ── Banner ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  HOME-HUNTER — LOCAL RUNNER")
    print("=" * 60)
    active_searches = [s for s in config.searches if s.enabled]
    print(f"  Active searches: {len(active_searches)}")
    for s in active_searches:
        print(f"    - [{s.site.upper() if s.site else 'UNKNOWN'}] {s.name}")
    print(f"  Headless       : {args.headless}")
    print(f"  Local seen DB  : {LOCAL_SEEN_FILE}")
    tg = config.notifications.telegram
    print(f"  Telegram       : {'ON' if tg.enabled else 'OFF'}")

    mode_flags = []
    if args.reset_tele:
        mode_flags.append("RESET-TELE")
    if args.refilter:
        mode_flags.append("REFILTER (no browser)")
    if mode_flags:
        print(f"  Mode           : {' + '.join(mode_flags)}")
    print("=" * 60 + "\n")

    # ── Mode: --reset-tele (và/hoặc --refilter) ─────────────────────────────
    if args.reset_tele or args.refilter:
        seen = _load_local_seen()

        if args.reset_tele:
            seen = reset_tele_sent(seen, search_names=[args.search] if args.search else None)
            _save_local_seen(seen)
            logger.info("[Reset] DB đã được reset. Các listing sẽ được gửi lại.")

        if args.refilter:
            listing_filter = ListingFilter(config.filters)
            telegram = TelegramNotifier(config.notifications.telegram)
            geocoder = GeocoderService()
            seen = run_refilter(
                config=config,
                target_name=args.search,
                seen=seen,
                listing_filter=listing_filter,
                telegram=telegram,
                geocoder=geocoder,
            )
            _save_local_seen(seen)

        if not args.refilter:
            logger.info("[Reset] Xong. Chạy lại script (không có --reset-tele) để scrape và gửi.")

        logger.info("Done.")
        return

    # ── Mode: Scrape bình thường ─────────────────────────────────────────────
    run_all(config, target_name=args.search, headless=args.headless)
    logger.info("Done.")

if __name__ == "__main__":
    main()
