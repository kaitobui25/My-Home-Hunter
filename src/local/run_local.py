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
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

# ── Ensure project root is on the path khi chạy trực tiếp ──────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _configure_console_encoding() -> None:
    """Make console output safe on Windows terminals that use legacy code pages."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_console_encoding()

from src.config import load_config, AppConfig, SearchConfig
from src.filter import ListingFilter
from src.notifier.telegram import TelegramNotifier
from src.geocoder import GeocoderService
from src.scraper.school_vacancy_hunter import SchoolVacancyHunter

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

# Save-frequency: reduce I/O pressure during filtering loop
SAVE_EVERY_LISTINGS = 500
SAVE_EVERY_SECONDS = 15


# ── URL helpers ──────────────────────────────────────────────────────────────

def _canonical_url(url: str) -> str:
    """
    Strip session/tracking query parameters so URLs from different scrape runs
    can be compared reliably.

    Example: SUUMO appends ?bc=XXXXXXXXXX which rotates every session.
      https://suumo.jp/chintai/jnc_123/?bc=111  ←→  https://suumo.jp/chintai/jnc_123/?bc=999
    Both refer to the same listing — stripping the query string makes them equal.
    """
    parsed = urlparse(url or "")
    return urlunparse(parsed._replace(query="", fragment=""))


# ── Seen-listings helpers ─────────────────────────────────────────────────────

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
        
        # ── Migration: Normalize existing keys to canonical URLs ──────────
        migrated = {}
        migration_count = 0
        for url, entry in data.items():
            canon = _canonical_url(url)
            if canon != url:
                migration_count += 1
            
            # If multiple old URLs map to same canonical URL, 
            # keep the one that was successfully sent to Telegram
            if canon not in migrated or entry.get("tele_sent"):
                # Update the internal URL to canonical as well
                entry["url"] = canon
                entry["_migrated"] = True
                migrated[canon] = entry
        
        if migration_count > 0:
            logger.info("[Local] Migrated %d legacy URLs to canonical format", migration_count)
            data = migrated

        tele_sent_count = sum(1 for v in data.values() if v.get("tele_sent"))
        logger.info(
            "[Local] Loaded %d seen listings (%d sent to Telegram) from: %s",
            len(data), tele_sent_count, LOCAL_SEEN_FILE,
        )
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("[Local] seen_listings corrupted or unreadable: %s — resetting.", e)
        return {}


def _atomic_replace_with_retry(src: str, dst: str, attempts: int = 5, delay: float = 0.05) -> None:
    """Atomically replace dst with src, retrying on Windows file-lock errors (PermissionError/OSError)."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except (PermissionError, OSError) as e:
            if i == attempts - 1:
                raise
            time.sleep(delay * (2 ** i))


def _save_local_seen(seen: dict) -> None:
    """Persist the local seen_listings store vào results-local/ using atomic write with replace-retry."""
    started = time.perf_counter()
    try:
        os.makedirs(RESULTS_LOCAL_DIR, exist_ok=True)
        # Write to a temp file in the same directory then atomically replace.
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(LOCAL_SEEN_FILE) + ".tmp.", dir=RESULTS_LOCAL_DIR)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(seen, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            _atomic_replace_with_retry(tmp_path, LOCAL_SEEN_FILE)
        except Exception:
            # Cleanup temp file on failure
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise
        elapsed = time.perf_counter() - started
        tele_sent_count = sum(1 for v in seen.values() if v.get("tele_sent"))
        logger.info(
            "[Local] Saved %d seen listings (%d sent) in %.1fs: %s",
            len(seen), tele_sent_count, elapsed, LOCAL_SEEN_FILE,
        )
    except IOError as e:
        elapsed = time.perf_counter() - started
        logger.error("[Local] Failed to save seen_listings in %.1fs: %s", elapsed, e)


def _mark_as_seen(seen: dict, listing: dict, tele_sent: bool) -> dict:
    """
    Đăng ký một listing vào seen store.
    - tele_sent=True: đã gửi Telegram thành công
    - tele_sent=False: đã scrape nhưng chưa gửi hoặc bị filter
    """
    url = listing.get("url")
    if not url:
        return seen
    
    # Use canonical URL as the stable key
    canon_url = _canonical_url(url)
    
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    entry = {
        **listing,
        "url": canon_url,  # Ensure internal URL is also canonical
        "tele_sent": tele_sent,
        "tele_sent_at": now_iso if tele_sent else None,
        "first_seen_at": seen.get(canon_url, {}).get("first_seen_at", now_iso),
    }
    seen[canon_url] = entry
    return seen


# ── Progress emission ─────────────────────────────────────────────────────────

def _emit_progress(**data):
    """Print a machine-readable progress line for the web UI to parse."""
    print("HH_PROGRESS " + json.dumps(data, ensure_ascii=False), flush=True)


_LAST_HEARTBEAT: float = 0.0


def _emit_phase(phase: str, search_name: str | None = None, message: str | None = None, **extra):
    """Emit a named phase event so the web UI always knows what the scraper is doing."""
    payload: dict = {"phase": phase}
    if search_name:
        payload["search_name"] = search_name
    if message:
        payload["message"] = message
    payload.update(extra)
    _emit_progress(**payload)
    logger.info("[Phase] %s%s", phase, f" - {message}" if message else "")


# ── Core runner ──────────────────────────────────────────────────────────────

def run_local_search(
    search: SearchConfig,
    config: AppConfig,
    seen: dict,
    listing_filter: ListingFilter,
    telegram: TelegramNotifier,
    geocoder: GeocoderService,
    headless: bool,
    on_seen_updated=None,
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
        elif site == "homes":
            if search.type == "rental":
                from src.scraper.homes_rental_hunter import HOMESRentalHunter
                hunter = HOMESRentalHunter(search=search, general=config.general, filters=config.filters)
            else:
                raise ValueError(f"HOMES site only supports 'rental' type, got: '{search.type}'.")
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

    # Compute canonical URL set for the delist pass in run_all().
    # Empty = scrape likely failed, run_all() will skip delist for this site.
    canonical_scraped: set[str] = {
        _canonical_url(l["url"]) for l in all_listings if l.get("url")
    }

    # ── Deduplicate: process listings that are new or still waiting for Telegram
    new_listings = [
        l for l in all_listings
        if l.get("url") and _canonical_url(l["url"]) not in seen
    ]
    pending_unsent = [
        entry for entry in seen.values()
        if not entry.get("tele_sent")
        and entry.get("url")
        and (
            entry.get("search_name") == search.name
            or entry.get("search_name") is None
        )
    ]

    # Merge new scraped listings with existing pending entries.
    # Prefer fresh scrape data for URLs found again this run.
    candidates: dict[str, dict] = {}
    for entry in pending_unsent:
        canon = _canonical_url(entry["url"])
        candidates[canon] = dict(entry)
    for listing in new_listings:
        canon = _canonical_url(listing["url"])
        if canon in candidates:
            listing["first_seen_at"] = candidates[canon].get("first_seen_at", listing.get("first_seen_at"))
        candidates[canon] = listing

    candidate_listings = list(candidates.values())

    logger.info(
        "[%s] Result: %d total | %d new | %d pending unsent",
        search.name, len(all_listings), len(new_listings), len(pending_unsent),
    )
    logger.info(
        "[%s] Processing %d listings (geocode/filter/notify)...",
        search.name, len(candidate_listings),
    )

    loc_cfg = config.filters.location_filter

    # ── Xây dựng danh sách Fingerprint từ DB cũ để dedup ────────────────────
    # Sử dụng thuật toán fuzzy location (cùng giá, cùng diện tích, cách nhau <= 200m)
    seen_fps = []
    for item in seen.values():
        ilat = item.get("lat")
        ilng = item.get("lng")
        if ilat is not None and ilng is not None:
            seen_fps.append({
                "lat": ilat,
                "lng": ilng,
                "price": item.get("price_man_yen"),
                "size": item.get("size_m2")
            })

    # ── Filter candidate listings (new + pending unsent) ──────────────────────────────────────────────────
    matched = []
    total_candidates = len(candidate_listings)
    process_start_time = time.perf_counter()
    save_counter = 0
    last_save_time = time.perf_counter()
    filtered_count = 0
    distance_filtered_count = 0
    deduped_count = 0
    geocode_failed_count = 0
    no_address_count = 0

    _emit_phase("filtering_listings", search_name=search.name, message="Processing listings", total_listings=total_candidates)

    # Initial progress: indicate processing started
    _emit_progress(
        listing_progress=True,
        search_name=search.name,
        processed=0,
        total_listings=total_candidates,
        matched=0,
        filtered=0, distance_filtered=0, deduped=0,
        geocode_failed=0, no_address=0,
    )

    for idx, listing in enumerate(candidate_listings):
        listing_start = time.perf_counter()
        processed = idx + 1

        # outcome trackers (set before continue → visible in finally)
        is_matched = False
        filtered_out = False
        distance_filtered = False
        deduped = False
        geocode_failed = False
        no_address = False

        try:
            # Geocode or reuse existing coordinates
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
                if lat is None or lng is None:
                    geocode_failed = True
            else:
                no_address = True
                listing["lat"] = None
                listing["lng"] = None
                listing["distance_km"] = None

            listing["search_name"] = search.name
            _mark_as_seen(seen, listing, tele_sent=False)
            save_counter += 1
            now_t = time.perf_counter()
            if save_counter >= SAVE_EVERY_LISTINGS or (now_t - last_save_time) >= SAVE_EVERY_SECONDS:
                if callable(on_seen_updated):
                    on_seen_updated(seen)
                save_counter = 0
                last_save_time = now_t

            # ── Slow listing diagnostics ──
            listing_elapsed = time.perf_counter() - listing_start
            if listing_elapsed > 5:
                logger.warning(
                    "[%s] SLOW listing (%.1fs): %s | %s | %s",
                    search.name, listing_elapsed,
                    listing.get("name", "?"), address, listing.get("url", "?"),
                )

            if not listing_filter.matches(listing):
                filtered_out = True
                continue
            if loc_cfg.enabled:
                dist = listing.get("distance_km")
                if dist is None or dist > loc_cfg.max_distance_km:
                    logger.debug(
                        "FILTERED distance %.1f > max %.1f [%s]",
                        dist or 999, loc_cfg.max_distance_km, listing.get("url"),
                    )
                    distance_filtered = True
                    continue

            # Cross-portal deduplication bằng fingerprint fuzzy distance (< 200m)
            lat, lng = listing.get("lat"), listing.get("lng")
            price = listing.get("price_man_yen")
            size = listing.get("size_m2")

            is_dup = False
            if lat is not None and lng is not None and price is not None and size is not None:
                for fp in seen_fps:
                    if fp["price"] == price and fp["size"] == size:
                        dist = geocoder.calculate_distance(lat, lng, fp["lat"], fp["lng"])
                        if dist <= 0.2:
                            is_dup = True
                            break

                if is_dup:
                    logger.debug("DEDUPED (Fingerprint): %s -> %s", listing.get("name"), listing.get("url"))
                    deduped = True
                    continue

                seen_fps.append({"lat": lat, "lng": lng, "price": price, "size": size})

            matched.append(listing)
            is_matched = True
            logger.info(
                "[%s] MATCHED: %s (Dist: %s) - %s",
                search.name,
                listing.get("name"),
                f"{listing.get('distance_km', 0):.2f} km" if listing.get('distance_km') is not None else "N/A",
                listing.get("url"),
            )
        finally:
            # ── Accumulate counters ──
            if filtered_out:
                filtered_count += 1
            if distance_filtered:
                distance_filtered_count += 1
            if deduped:
                deduped_count += 1
            if geocode_failed:
                geocode_failed_count += 1
            if no_address:
                no_address_count += 1

            # ── Listing progress logging + HH_PROGRESS (runs for EVERY listing) ──
            if processed % 50 == 0 or processed == total_candidates:
                elapsed = time.perf_counter() - process_start_time
                logger.info(
                    "[%s] Listing progress: %d/%d processed | matched=%d | filtered=%d | distance=%d | deduped=%d | geocode_failed=%d | elapsed=%.1fs",
                    search.name, processed, total_candidates, len(matched),
                    filtered_count, distance_filtered_count, deduped_count,
                    geocode_failed_count, elapsed,
                )
                _emit_progress(
                    listing_progress=True,
                    search_name=search.name,
                    processed=processed,
                    total_listings=total_candidates,
                    matched=len(matched),
                    filtered=filtered_count,
                    distance_filtered=distance_filtered_count,
                    deduped=deduped_count,
                    geocode_failed=geocode_failed_count,
                    no_address=no_address_count,
                )

    # ── Emit filter_done with final counters ──
    _emit_phase(
        "filter_done",
        search_name=search.name,
        message="Listing filtering completed",
        processed=total_candidates,
        total_listings=total_candidates,
        matched=len(matched),
        filtered=filtered_count,
        distance_filtered=distance_filtered_count,
        deduped=deduped_count,
        geocode_failed=geocode_failed_count,
        no_address=no_address_count,
    )

    # ── Final batch save for non-Telegram listings ──
    _emit_phase("saving_seen_db", search_name=search.name, message="Saving local seen DB")
    if save_counter > 0 and callable(on_seen_updated):
        on_seen_updated(seen)
    _emit_phase("saved_seen_db", search_name=search.name, message="Local seen DB saved")

    logger.info(
        "[%s] %d new | %d matched filter → sending Telegram",
        search.name, len(new_listings), len(matched),
    )

    # ── Notify và Persist seen ─────────────────────────────────────────────────────────────
    if matched:
        _emit_phase("notifying_telegram", search_name=search.name, message=f"Sending {len(matched)} listings to Telegram")
        sent_count = telegram.send_batch(matched, search_name=f"[LOCAL] {search.name}")
        _emit_phase("telegram_done", search_name=search.name, message=f"Sent {sent_count}/{len(matched)} listings", matched=len(matched), sent=sent_count)
        if sent_count > 0:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            for listing in matched[:sent_count]:
                _mark_as_seen(seen, listing, tele_sent=True)
            if callable(on_seen_updated):
                on_seen_updated(seen)
        if sent_count < len(matched):
            logger.warning(
                "[%s] Only %d/%d matched listings were sent. Remaining will retry.",
                search.name, sent_count, len(matched),
            )
    else:
        logger.info("[%s] No matching new listings to notify.", search.name)
        _emit_phase("telegram_done", search_name=search.name, message="No listings to notify", matched=0, sent=0)

    return seen, canonical_scraped


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
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
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

    # Per-site canonical URLs collected across ALL searches (for delist pass)
    site_canonical: dict[str, set] = {}  # e.g. {"suumo": {url1, url2}, "nifty": {...}}

    _emit_progress(run_start=True, total=len(active_searches))
    _run_start_time = time.perf_counter()

    for i, search in enumerate(active_searches):
        search_start_time = time.perf_counter()
        _emit_progress(
            search_start=True,
            search_name=search.name,
            site=search.site or "suumo",
            index=i + 1,
            total=len(active_searches),
        )
        _emit_phase("scraping", search_name=search.name, message=f"Scraping {search.site or 'suumo'}")

        seen, canonical_urls = run_local_search(
            search=search,
            config=config,
            seen=seen,
            listing_filter=listing_filter,
            telegram=telegram,
            geocoder=geocoder,
            headless=headless,
            on_seen_updated=_save_local_seen,
        )
        # Accumulate canonical URLs per site (only when scrape returned results)
        if canonical_urls:
            sk = search.site or "suumo"
            site_canonical.setdefault(sk, set()).update(canonical_urls)

        search_elapsed = time.perf_counter() - search_start_time
        _emit_phase("search_done", search_name=search.name, message=f"Search completed in {search_elapsed:.0f}s", seconds=round(search_elapsed, 1), matched=len(canonical_urls))
        _emit_progress(
            search_done=True,
            search_name=search.name,
            site=search.site or "suumo",
            index=i + 1,
            total=len(active_searches),
            seconds=round(search_elapsed, 1),
        )

        _save_local_seen(seen)

        if i < len(active_searches) - 1:
            delay = config.general.delay_between_searches
            if delay > 0:
                logger.info("Sleeping %ds before next search...", delay)
                time.sleep(delay)

    total_elapsed = time.perf_counter() - _run_start_time
    _emit_progress(run_done=True, total_seconds=round(total_elapsed, 1))

    logger.info("All local searches completed. Total seen (cumulative): %d", len(seen))

    # ── Delist pass: compare ALL scraped URLs per site vs DB ────────────────────
    def _url_site(url: str) -> str | None:
        if "nifty.com" in url:  return "nifty"
        if "suumo.jp"  in url:  return "suumo"
        return None

    if site_canonical:
        _emit_phase("delist_pass", message="Checking for delisted listings")
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        count_dl = 0
        for url, entry in seen.items():
            sk = _url_site(url)
            if not sk or sk not in site_canonical:
                continue  # site not scraped this run — don't touch
            if _canonical_url(url) in site_canonical[sk]:
                if entry.get("delisted"):
                    entry["delisted"] = False
                    entry["delisted_at"] = None
                    logger.info("Re-listed (restored): %s", entry.get("name", url))
            else:
                if not entry.get("delisted"):
                    entry["delisted"] = True
                    entry["delisted_at"] = now_iso
                    count_dl += 1
                    logger.debug("Delisting: %s", entry.get("name", url))
        _emit_phase("delist_done", message=f"Delist pass complete: {count_dl} listings marked as gone", delisted=count_dl)
        if count_dl:
            logger.info("Delist pass: marked %d listing(s) as gone from site.", count_dl)
        _save_local_seen(seen)

    # ── Part 2: School vacancy searches ─────────────────────────────────────
    if config.list_schools:
        active_schools = [s for s in config.list_schools if s.enabled]
        if active_schools:
            _emit_phase("school_search", message=f"Running {len(active_schools)} school vacancy search(es)")
            logger.info("Running %d school vacancy search(es) locally", len(active_schools))
            for school_search in active_schools:
                try:
                    hunter = SchoolVacancyHunter(
                        search=school_search,
                        general=config.general,
                        geocoder=geocoder
                    )
                    results = hunter.scrape_vacancies()
                    if results:
                        # Map to output file path (fixed for now as requested)
                        # We can make this dynamic if needed
                        output_path = os.path.join(
                            _PROJECT_ROOT, "my-data", "hoikuen", "ninka", 
                            "yodogawa_vacancies_1yo_20260501.json"
                        )
                        addr_map_path = os.path.join(
                            _PROJECT_ROOT, "my-data", "hoikuen", "ninka", 
                            "school_addresses.json"
                        )
                        hunter.update_json(results, output_path, addr_map_path)
                    else:
                        logger.info("[%s] No vacancies found or scrape empty.", school_search.name)
                except Exception as e:
                    logger.error("School search [%s] failed: %s", school_search.name, e)
            _emit_phase("school_search_done", message="School vacancy searches completed")

    _emit_phase("run_done", message=f"All done in {total_elapsed:.0f}s", total_seconds=round(total_elapsed, 1))


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
    print("  HOME-HUNTER - LOCAL RUNNER")
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
