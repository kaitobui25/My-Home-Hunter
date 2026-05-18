"""
src/web/app.py
============================
Flask web server for Home Hunter map visualization.

Cách chạy (từ thư mục gốc My-Home-Hunter):
    python -m src.web.app

Mở trình duyệt: http://localhost:5001

Tính năng:
  - Đọc results-local/local_seen_listings.json (tracking DB)
  - Resolve tọa độ từ results/geocode_cache.json (không gọi API thêm)
  - Áp dụng filter y hệt config.yaml (dùng lại ListingFilter)
  - Trả về JSON qua /api/listings để frontend render bản đồ
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading

from flask import Flask, jsonify, render_template, request

# ── Ensure project root is on the path ──────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.config import load_config
from src.filter import ListingFilter

# ── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates")

# ── Paths ────────────────────────────────────────────────────────────────────
LOCAL_SEEN_FILE = os.path.join(
    _PROJECT_ROOT, "results-local", "local_seen_listings.json"
)
GEOCODE_CACHE_FILE = os.path.join(_PROJECT_ROOT, "results", "geocode_cache.json")
CONFIG_FILE = os.path.join(_PROJECT_ROOT, "config.yaml")
SCHOOL_DATA_FILE = os.path.join(
    _PROJECT_ROOT, "my-data", "hoikuen", "ninka", "yodogawa_vacancies_1yo_20260501.json"
)
NINKAGAI_DATA_FILE = os.path.join(
    _PROJECT_ROOT, "my-data", "hoikuen", "ninkagai", "ninkagai_geocoded.json"
)

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("web.app")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_geocode_cache() -> dict:
    """Load geocode cache. Returns {clean_address: [lat, lng]}."""
    try:
        with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("Geocode cache not found: %s", GEOCODE_CACHE_FILE)
        return {}
    except Exception as e:
        logger.error("Failed to load geocode cache: %s", e)
        return {}


def _clean_address(address: str) -> str:
    """
    Same address cleaning logic as GeocoderService.get_coordinates().
    Must stay in sync with src/geocoder.py.
    """
    return address.split(" ")[0].split("\n")[0]


def _resolve_coords(
    address: str, geocode_cache: dict
) -> tuple[float | None, float | None]:
    """Lookup lat/lng from geocode cache using the same clean key."""
    if not address:
        return None, None
    clean = _clean_address(address)
    result = geocode_cache.get(clean)
    if result and len(result) == 2:
        try:
            return float(result[0]), float(result[1])
        except (TypeError, ValueError):
            pass
    return None, None


def _calc_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float | None:
    """Calculate distance in km using geopy (already in requirements.txt)."""
    try:
        from geopy.distance import geodesic

        return geodesic((lat1, lng1), (lat2, lng2)).kilometers
    except Exception:
        return None


# ── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("map.html")


@app.route("/api/listings")
def api_listings():
    """
    Main API endpoint. Returns:
    {
        "listings": [...],  # entries that pass all filters and have coordinates
        "stats": {...},     # counts for debugging
        "config": {...}     # filter settings for display in UI
    }
    """
    try:
        # Load config + filter
        config = load_config(CONFIG_FILE)
        listing_filter = ListingFilter(config.filters)
        loc_cfg = config.filters.location_filter

        # Load geocode cache (offline — no API calls)
        geocode_cache = _load_geocode_cache()

        # Load seen listings DB
        if not os.path.exists(LOCAL_SEEN_FILE):
            return jsonify(
                {
                    "error": f"Không tìm thấy file: {LOCAL_SEEN_FILE}",
                    "listings": [],
                    "stats": {},
                    "config": {},
                }
            ), 404

        with open(LOCAL_SEEN_FILE, "r", encoding="utf-8") as f:
            seen_data: dict = json.load(f)

        # ── Process each entry ──────────────────────────────────────────────
        results = []
        count_no_data = 0  # Migrated/empty entries without listing fields
        count_no_coords = 0  # Has data but address not in geocode cache
        count_filtered = 0  # Has coords but fails ListingFilter or location filter
        count_delisted = 0  # Removed from source site (marked by run_local)

        for url, entry in seen_data.items():
            # Skip legacy migrated entries with no listing data
            # A real listing entry has at least "address" or "name" from the scraper
            if not entry.get("address") and not entry.get("name"):
                count_no_data += 1
                continue

            # Skip listings that run_local confirmed are no longer on the source site
            if entry.get("delisted"):
                count_delisted += 1
                continue

            # Resolve coordinates from geocode cache
            address = entry.get("address", "")
            lat, lng = _resolve_coords(address, geocode_cache)

            if lat is None or lng is None:
                count_no_coords += 1
                continue

            # Calculate distance from center
            distance_km = None
            if loc_cfg.enabled:
                distance_km = _calc_distance(
                    lat, lng, loc_cfg.center_lat, loc_cfg.center_lng
                )

            # Inject coords into entry so ListingFilter can work with it
            entry["lat"] = lat
            entry["lng"] = lng
            entry["distance_km"] = distance_km

            # Apply ListingFilter (size, age, rent, layout, admin fee, etc.)
            if not listing_filter.matches(entry):
                count_filtered += 1
                continue

            # Apply location distance filter
            if loc_cfg.enabled:
                if distance_km is None or distance_km > loc_cfg.max_distance_km:
                    count_filtered += 1
                    continue

            # ── Build output record ─────────────────────────────────────────
            results.append(
                {
                    "name": entry.get("name") or "物件名不明",
                    "url": url,
                    "lat": lat,
                    "lng": lng,
                    "distance_km": round(distance_km, 3)
                    if distance_km is not None
                    else None,
                    "price_man_yen": entry.get("price_man_yen"),
                    "admin_fee_yen": entry.get("admin_fee_yen"),
                    "deposit_man_yen": entry.get("deposit_man_yen"),
                    "key_money_man_yen": entry.get("key_money_man_yen"),
                    "layout": entry.get("layout", ""),
                    "size_m2": entry.get("size_m2"),
                    "floor": entry.get("floor", ""),
                    "building_age": entry.get("building_age"),
                    "building_age_raw": entry.get("building_age_raw", ""),
                    "address": address,
                    "transportation": entry.get("transportation", ""),
                    "tele_sent": bool(entry.get("tele_sent", False)),
                    "first_seen_at": entry.get("first_seen_at"),
                    "search_name": entry.get("search_name", ""),
                }
            )

        # ── Deduplicate by property name: prefer Nifty over SUUMO ──────────
        # When the same 物件名 appears in both sources, keep only the Nifty
        # version — it carries richer data (floor plan detail, etc.).
        def _is_nifty(url: str) -> bool:
            return "nifty.com" in (url or "")

        # Pass 1: for each name, pick the best source (Nifty wins over SUUMO)
        name_to_best: dict = {}
        for item in results:
            name = (item.get("name") or "").strip()
            if not name or name == "物件名不明":
                continue
            if name not in name_to_best:
                name_to_best[name] = item
            elif not _is_nifty(name_to_best[name]["url"]) and _is_nifty(item["url"]):
                name_to_best[name] = item  # upgrade to Nifty

        # Pass 2: rebuild results emitting only the best item per name
        seen_names: set = set()
        deduped: list = []
        for item in results:
            name = (item.get("name") or "").strip()
            if not name or name == "物件名不明":
                deduped.append(item)  # nameless: always keep
                continue
            if name in seen_names:
                continue  # duplicate name: skip
            seen_names.add(name)
            deduped.append(name_to_best[name])  # emit the winner (may differ from item)

        count_deduped = len(results) - len(deduped)
        results = deduped

        # ── Pass 3: fuzzy spatial + price/size dedup ─────────────────────────
        # Listings within ~200m distance with identical (price_man_yen, size_m2)
        # are almost certainly the same unit listed by multiple portals.
        # Keep only the best source (Nifty wins; within the same source, first seen wins).
        dedup_groups = []
        for item in results:
            price = item.get("price_man_yen")
            size = item.get("size_m2")
            if price is None and size is None:
                dedup_groups.append([item])
                continue

            lat, lng = item.get("lat"), item.get("lng")
            if lat is None or lng is None:
                dedup_groups.append([item])
                continue

            # Find matching group
            matched_group = None
            for group in dedup_groups:
                g_item = group[0]
                if (
                    g_item.get("price_man_yen") == price
                    and g_item.get("size_m2") == size
                ):
                    g_lat, g_lng = g_item.get("lat"), g_item.get("lng")
                    if g_lat is not None and g_lng is not None:
                        dist = _calc_distance(lat, lng, g_lat, g_lng)
                        if dist <= 0.2:  # within 200 meters
                            matched_group = group
                            break

            if matched_group is not None:
                matched_group.append(item)
            else:
                dedup_groups.append([item])

        deduped2 = []
        for group in dedup_groups:
            if len(group) == 1:
                group[0]["other_urls"] = []
                deduped2.append(group[0])
            else:
                # Ưu tiên item có địa chỉ dài nhất (địa chỉ chi tiết tới tận số nhà)
                best = max(group, key=lambda x: len(x.get("address", "")))

                # Gom tất cả url khác của các nền tảng khác để nhúng vào popup
                other_urls = list(
                    {
                        item["url"]
                        for item in group
                        if item["url"] != best["url"] and item.get("url")
                    }
                )
                best["other_urls"] = other_urls
                deduped2.append(best)

        count_deduped += len(results) - len(deduped2)
        results = deduped2

        logger.info(
            "API /listings → total_db=%d | matched=%d | deduped=%d | delisted=%d | no_data=%d | no_coords=%d | filtered=%d",
            len(seen_data),
            len(results),
            count_deduped,
            count_delisted,
            count_no_data,
            count_no_coords,
            count_filtered,
        )

        return jsonify(
            {
                "listings": results,
                "stats": {
                    "total_in_db": len(seen_data),
                    "matched": len(results),
                    "no_data": count_no_data,
                    "no_coords": count_no_coords,
                    "filtered_out": count_filtered,
                },
                "config": {
                    "center_lat": loc_cfg.center_lat,
                    "center_lng": loc_cfg.center_lng,
                    "max_distance_km": loc_cfg.max_distance_km,
                    "location_filter_enabled": loc_cfg.enabled,
                    "max_rent_man_yen": config.filters.rental.max_rent_man_yen,
                    "allowed_layouts": config.filters.rental.allowed_layouts,
                    "max_building_age_years": config.filters.max_building_age_years,
                    "min_size_m2": config.filters.min_size_m2,
                    "max_admin_fee_yen": config.filters.rental.max_admin_fee_yen,
                },
            }
        )

    except FileNotFoundError as e:
        logger.error("File not found: %s", e)
        return jsonify(
            {"error": str(e), "listings": [], "stats": {}, "config": {}}
        ), 404
    except Exception as e:
        logger.exception("Unexpected error in /api/listings")
        return jsonify(
            {"error": str(e), "listings": [], "stats": {}, "config": {}}
        ), 500


@app.route("/api/schools")
def api_schools():
    """
    Returns nursery school vacancy data for map display.
    Only schools that have lat/lng coordinates are returned.
    """
    try:
        if not os.path.exists(SCHOOL_DATA_FILE):
            return jsonify({"schools": [], "stats": {"total": 0, "with_coords": 0}})

        with open(SCHOOL_DATA_FILE, "r", encoding="utf-8") as f:
            raw: list = json.load(f)

        schools = []
        for s in raw:
            lat = s.get("lat")
            lng = s.get("lng")
            if lat is None or lng is None:
                continue
            schools.append(
                {
                    "name": s.get("school_name", "不明"),
                    "address": s.get("address", ""),
                    "lat": lat,
                    "lng": lng,
                    "vacancies": s.get("vacancies_1_year", 0),
                    "notes": s.get("notes", ""),
                    "scraped_at": s.get("scraped_at", ""),
                }
            )

        logger.info(
            "API /schools → %d schools with coords (of %d total)",
            len(schools),
            len(raw),
        )
        return jsonify(
            {
                "schools": schools,
                "stats": {"total": len(raw), "with_coords": len(schools)},
            }
        )

    except Exception as e:
        logger.exception("Error in /api/schools")
        return jsonify({"error": str(e), "schools": []}), 500


@app.route("/api/ninkagai")
def api_ninkagai():
    """
    Returns non-accredited nursery school (ninkagai) location data for map display.
    """
    try:
        if not os.path.exists(NINKAGAI_DATA_FILE):
            return jsonify({"schools": [], "stats": {"total": 0}})

        with open(NINKAGAI_DATA_FILE, "r", encoding="utf-8") as f:
            raw: list = json.load(f)

        schools = []
        for s in raw:
            lat = s.get("lat")
            lng = s.get("lng")
            if lat is None or lng is None:
                continue
            schools.append(
                {
                    "name": s.get("name", "不明"),
                    "address": s.get("address", ""),
                    "lat": lat,
                    "lng": lng,
                }
            )

        logger.info("API /ninkagai → %d schools", len(schools))
        return jsonify(
            {
                "schools": schools,
                "stats": {"total": len(raw), "with_coords": len(schools)},
            }
        )

    except Exception as e:
        logger.exception("Error in /api/ninkagai")
        return jsonify({"error": str(e), "schools": []}), 500


# ── Config edit endpoints ─────────────────────────────────────────────────────

_EDITABLE_KEYS = {
    # yaml_key: (type, nullable)
    "max_distance_km": (float, False),
    "max_rent_man_yen": (float, True),
    "min_size_m2": (float, True),
    "max_building_age_years": (int, True),
    "center_lat": (float, False),
    "center_lng": (float, False),
}


def _yaml_set(content: str, key: str, value) -> str:
    """Replace the scalar value of *key* in YAML text, preserving comments."""
    # Matches:  <indent>key: <old_value>  # optional comment
    pattern = rf"^([ \t]*{re.escape(key)}[ \t]*:[ \t]*)([^\n#]+?)([\t ]*(#[^\n]*)?)$"
    replacement = rf"\g<1>{value}\g<3>"
    new, n = re.subn(pattern, replacement, content, flags=re.MULTILINE)
    if n == 0:
        logger.warning("_yaml_set: key %r not found in config", key)
    return new


@app.route("/api/config-edit", methods=["GET"])
def api_config_edit_get():
    """Return current values of the editable filter fields."""
    try:
        config = load_config(CONFIG_FILE)
        loc = config.filters.location_filter
        return jsonify(
            {
                "max_distance_km": loc.max_distance_km,
                "max_rent_man_yen": config.filters.rental.max_rent_man_yen,
                "min_size_m2": config.filters.min_size_m2,
                "max_building_age_years": config.filters.max_building_age_years,
                "center_lat": loc.center_lat,
                "center_lng": loc.center_lng,
            }
        )
    except Exception as e:
        logger.exception("Error in GET /api/config-edit")
        return jsonify({"error": str(e)}), 500


@app.route("/api/config-edit", methods=["POST"])
def api_config_edit_post():
    """Patch the 4 editable filter fields in config.yaml in-place."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        applied = {}
        for key, (cast, nullable) in _EDITABLE_KEYS.items():
            if key not in data:
                continue
            raw = data[key]
            if raw is None or raw == "":
                if nullable:
                    value = "null"
                else:
                    continue  # skip — field is required
            else:
                try:
                    value = cast(raw)
                    # Format cleanly: no trailing .0 for int, 1 decimal for float
                    if cast is int:
                        value = int(value)
                    else:
                        # For coordinates, keep more precision; for others, 2 decimals is enough
                        if key in ("center_lat", "center_lng"):
                            value = f"{float(value):.6f}".rstrip("0").rstrip(".")
                            if "." not in value:
                                value += ".0"
                        else:
                            # Keep up to 2 decimals, strip trailing zeros, ensure ≥1 decimal
                            s = f"{float(value):.2f}".rstrip("0").rstrip(".")
                            value = s if "." in s else s + ".0"
                except (TypeError, ValueError) as exc:
                    return jsonify({"error": f"Invalid value for {key}: {exc}"}), 400

            content = _yaml_set(content, key, value)
            applied[key] = value

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info("Config updated via web: %s", applied)
        return jsonify({"ok": True, "updated": applied})

    except Exception as e:
        logger.exception("Error in POST /api/config-edit")
        return jsonify({"error": str(e)}), 500


# ── Scraper endpoints ───────────────────────────────────────────────────────────────

_scrape_lock = threading.Lock()
_scrape_job: dict = {"status": "idle", "log": [], "returncode": None, "proc": None}


def _drain_proc(proc, job: dict) -> None:
    """Background thread: collect output and update job status when done."""
    for line in proc.stdout:
        with _scrape_lock:
            job["log"].append(line.rstrip("\n"))
    proc.wait()
    with _scrape_lock:
        job["returncode"] = proc.returncode
        if job.get("status") == "stopping":
            job["status"] = "stopped"
        else:
            job["status"] = "done" if proc.returncode == 0 else "error"
        job["proc"] = None
    logger.info("Scraper finished with returncode=%s", proc.returncode)


@app.route("/api/scrape/start", methods=["POST"])
def api_scrape_start():
    """Launch run_local --headless in a background subprocess."""
    with _scrape_lock:
        if _scrape_job["status"] == "running":
            return jsonify({"error": "Scraper is already running"}), 409
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.local.run_local", "--headless"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=_PROJECT_ROOT,
            )
            _scrape_job.update(
                {
                    "status": "running",
                    "log": [],
                    "returncode": None,
                    "proc": proc,
                }
            )
            threading.Thread(
                target=_drain_proc, args=(proc, _scrape_job), daemon=True
            ).start()
            logger.info("Scraper started (pid=%s)", proc.pid)
            return jsonify({"ok": True})
        except Exception as e:
            logger.exception("Failed to start scraper")
            _scrape_job["status"] = "error"
            return jsonify({"error": str(e)}), 500


@app.route("/api/scrape/stop", methods=["POST"])
def api_scrape_stop():
    """Stop the running scraper subprocess."""
    with _scrape_lock:
        proc = _scrape_job.get("proc")
        if _scrape_job.get("status") != "running" or proc is None:
            return jsonify({"error": "Scraper is not running"}), 409
        _scrape_job["status"] = "stopping"
        _scrape_job["log"].append("[web] Stop requested by user...")

    try:
        proc.terminate()
    except Exception as e:
        logger.exception("Failed to terminate scraper process")
        with _scrape_lock:
            _scrape_job["status"] = "error"
            _scrape_job["log"].append(f"[web] Stop failed: {e}")
        return jsonify({"error": str(e)}), 500

    logger.info("Stop signal sent to scraper (pid=%s)", proc.pid)
    return jsonify({"ok": True})


@app.route("/api/scrape/status", methods=["GET"])
def api_scrape_status():
    """Return current scraper status + last 30 log lines."""
    with _scrape_lock:
        return jsonify(
            {
                "status": _scrape_job["status"],
                "returncode": _scrape_job["returncode"],
                "log": _scrape_job["log"][-30:],
            }
        )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 52)
    print("  HOME HUNTER — MAP VIEWER")
    print("=" * 52)
    print(f"  Data   : {LOCAL_SEEN_FILE}")
    print(f"  Config : {CONFIG_FILE}")
    print(f"  Cache  : {GEOCODE_CACHE_FILE}")
    print(f"  URL    : http://localhost:5001")
    print("=" * 52 + "\n")
    app.run(host="0.0.0.0", port=5001, debug=False)
