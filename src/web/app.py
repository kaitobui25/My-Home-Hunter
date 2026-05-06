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
import sys

from flask import Flask, jsonify, render_template

# ── Ensure project root is on the path ──────────────────────────────────────
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.config import load_config
from src.filter import ListingFilter

# ── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates")

# ── Paths ────────────────────────────────────────────────────────────────────
LOCAL_SEEN_FILE = os.path.join(_PROJECT_ROOT, "results-local", "local_seen_listings.json")
GEOCODE_CACHE_FILE = os.path.join(_PROJECT_ROOT, "results", "geocode_cache.json")
CONFIG_FILE = os.path.join(_PROJECT_ROOT, "config.yaml")
SCHOOL_DATA_FILE = os.path.join(_PROJECT_ROOT, "my-data", "hoikuen", "ninka", "yodogawa_vacancies_1yo_20260501.json")
NINKAGAI_DATA_FILE = os.path.join(_PROJECT_ROOT, "my-data", "hoikuen", "ninkagai", "ninkagai_geocoded.json")

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


def _resolve_coords(address: str, geocode_cache: dict) -> tuple[float | None, float | None]:
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
            return jsonify({
                "error": f"Không tìm thấy file: {LOCAL_SEEN_FILE}",
                "listings": [],
                "stats": {},
                "config": {}
            }), 404

        with open(LOCAL_SEEN_FILE, "r", encoding="utf-8") as f:
            seen_data: dict = json.load(f)

        # ── Process each entry ──────────────────────────────────────────────
        results = []
        count_no_data = 0      # Migrated/empty entries without listing fields
        count_no_coords = 0    # Has data but address not in geocode cache
        count_filtered = 0     # Has coords but fails ListingFilter or location filter

        for url, entry in seen_data.items():
            # Skip legacy migrated entries with no listing data
            # A real listing entry has at least "address" or "name" from the scraper
            if not entry.get("address") and not entry.get("name"):
                count_no_data += 1
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
                distance_km = _calc_distance(lat, lng, loc_cfg.center_lat, loc_cfg.center_lng)

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
            results.append({
                "name": entry.get("name") or "物件名不明",
                "url": url,
                "lat": lat,
                "lng": lng,
                "distance_km": round(distance_km, 3) if distance_km is not None else None,
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
            })

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
                deduped.append(item)   # nameless: always keep
                continue
            if name in seen_names:
                continue               # duplicate name: skip
            seen_names.add(name)
            deduped.append(name_to_best[name])  # emit the winner (may differ from item)

        count_deduped = len(results) - len(deduped)
        results = deduped

        logger.info(
            "API /listings → total_db=%d | matched=%d | deduped=%d | no_data=%d | no_coords=%d | filtered=%d",
            len(seen_data), len(results), count_deduped, count_no_data, count_no_coords, count_filtered,
        )

        return jsonify({
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
        })

    except FileNotFoundError as e:
        logger.error("File not found: %s", e)
        return jsonify({"error": str(e), "listings": [], "stats": {}, "config": {}}), 404
    except Exception as e:
        logger.exception("Unexpected error in /api/listings")
        return jsonify({"error": str(e), "listings": [], "stats": {}, "config": {}}), 500


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
            schools.append({
                "name": s.get("school_name", "不明"),
                "address": s.get("address", ""),
                "lat": lat,
                "lng": lng,
                "vacancies": s.get("vacancies_1_year", 0),
                "notes": s.get("notes", ""),
                "scraped_at": s.get("scraped_at", ""),
            })

        logger.info("API /schools → %d schools with coords (of %d total)", len(schools), len(raw))
        return jsonify({
            "schools": schools,
            "stats": {"total": len(raw), "with_coords": len(schools)},
        })

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
            schools.append({
                "name": s.get("name", "不明"),
                "address": s.get("address", ""),
                "lat": lat,
                "lng": lng,
            })

        logger.info("API /ninkagai → %d schools", len(schools))
        return jsonify({
            "schools": schools,
            "stats": {"total": len(raw), "with_coords": len(schools)},
        })

    except Exception as e:
        logger.exception("Error in /api/ninkagai")
        return jsonify({"error": str(e), "schools": []}), 500


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
