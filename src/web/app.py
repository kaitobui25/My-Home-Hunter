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
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

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


def _load_json_retry(path: str, attempts: int = 3, delay: float = 0.1, fallback=None):
    """Read and parse JSON file with retries on JSONDecodeError. Returns fallback on failure."""
    if not os.path.exists(path):
        return fallback
    for i in range(attempts):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            if i == attempts - 1:
                logger.warning("JSON parsing failed for %s: %s", path, e)
                return fallback
            time.sleep(delay * (2 ** i))
        except Exception as e:
            logger.error("Error reading JSON %s: %s", path, e)
            return fallback


def _load_geocode_cache() -> dict:
    """Load geocode cache. Returns {clean_address: [lat, lng]}."""
    if not os.path.exists(GEOCODE_CACHE_FILE):
        logger.warning("Geocode cache not found: %s", GEOCODE_CACHE_FILE)
        return {}
    data = _load_json_retry(GEOCODE_CACHE_FILE, attempts=3, delay=0.1, fallback={})
    if not isinstance(data, dict):
        logger.error("Failed to load geocode cache (invalid format): %s", GEOCODE_CACHE_FILE)
        return {}
    return data


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

        seen_data = _load_json_retry(LOCAL_SEEN_FILE, attempts=3, delay=0.1, fallback={})

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

            # Resolve coordinates from stored entry first, otherwise fallback to geocode cache
            lat = entry.get("lat")
            lng = entry.get("lng")
            if lat is None or lng is None:
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

        raw: list = _load_json_retry(SCHOOL_DATA_FILE, attempts=3, delay=0.1, fallback=[])

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

        raw: list = _load_json_retry(NINKAGAI_DATA_FILE, attempts=3, delay=0.1, fallback=[])

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

SCRAPER_TIMEOUT_SECONDS = int(os.environ.get("HOME_HUNTER_SCRAPER_TIMEOUT_SECONDS", "900"))
SCRAPE_METRICS_FILE = os.path.join(_PROJECT_ROOT, "results-local", "scrape_metrics.json")

_scrape_lock = threading.Lock()
_scrape_job: dict = {
    "status": "idle",
    "log": [],
    "returncode": None,
    "proc": None,
    "pid": None,
    "started_at": None,
    "finished_at": None,
    "stop_requested": False,
    "timed_out": False,
}


def _terminate_process_tree(proc, grace_seconds=5):
    """Kill subprocess and all its children. Prefer psutil; fallback to taskkill on Windows."""
    pid = proc.pid
    try:
        import psutil

        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.terminate()
        except psutil.NoSuchProcess:
            pass
        gone, alive = psutil.wait_procs(
            children + [parent], timeout=grace_seconds
        )
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        return
    except ImportError:
        pass

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(grace_seconds)
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def _try_parse_progress(text: str, job: dict) -> None:
    """Parse HH_PROGRESS lines from scraper stdout into job progress_data. Never raises."""
    if not text.startswith("HH_PROGRESS "):
        return
    try:
        data = json.loads(text[len("HH_PROGRESS "):])
    except (json.JSONDecodeError, ValueError):
        return

    if data.get("run_start"):
        job["progress_data"] = dict(data)
        job["search_times"] = []
        return

    if "progress_data" not in job:
        job["progress_data"] = {}
    job["progress_data"].update(data)

    if data.get("search_done"):
        if "search_times" not in job:
            job["search_times"] = []
        job["search_times"].append({
            "search_name": data.get("search_name"),
            "site": data.get("site"),
            "seconds": data.get("seconds"),
        })


def _reader_thread_fn(proc, job):
    """Read stdout lines into job log (daemon — does not set final status)."""
    try:
        for line in proc.stdout:
            with _scrape_lock:
                raw = line.rstrip("\n")
                job["log"].append(raw)
                if len(job["log"]) > 500:
                    job["log"] = job["log"][-500:]
                _try_parse_progress(raw, job)
    except ValueError:
        pass


def _save_scrape_metrics(job: dict) -> None:
    """Append a metrics entry for a completed successful run. Best-effort, never raises."""
    metrics = _load_json_retry(SCRAPE_METRICS_FILE, fallback=[])
    progress = job.get("progress_data", {})

    total_seconds = progress.get("total_seconds")
    if total_seconds is None:
        started_at = job.get("started_at")
        finished_at = job.get("finished_at")
        if started_at and finished_at:
            try:
                s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                f = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                total_seconds = (f - s).total_seconds()
            except (ValueError, AttributeError):
                pass

    entry = {
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "total_seconds": round(total_seconds, 1) if total_seconds is not None else None,
        "searches": job.get("search_times", []),
    }
    metrics.append(entry)
    if len(metrics) > 20:
        metrics = metrics[-20:]
    try:
        os.makedirs(os.path.dirname(SCRAPE_METRICS_FILE), exist_ok=True)
        with open(SCRAPE_METRICS_FILE, "w", encoding="utf-8") as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to save scrape metrics: %s", e)


def _monitor_thread_fn(proc, job):
    """Wait for process completion with timeout, then set final job status."""
    try:
        proc.wait(timeout=SCRAPER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(proc)
        proc.wait()
        with _scrape_lock:
            job["status"] = "timeout"
            job["timed_out"] = True
            job["returncode"] = proc.returncode
            job["finished_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            job["log"].append(
                f"[web] Scraper timed out after {SCRAPER_TIMEOUT_SECONDS}s. Process tree killed."
            )
            job["proc"] = None
        logger.info("Scraper timed out after %ds", SCRAPER_TIMEOUT_SECONDS)
        return

    with _scrape_lock:
        job["returncode"] = proc.returncode
        job["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if job.get("stop_requested"):
            job["status"] = "stopped"
        elif proc.returncode == 0:
            job["status"] = "done"
        else:
            job["status"] = "error"
        job["proc"] = None

    if job["status"] == "done":
        _save_scrape_metrics(job)
    logger.info("Scraper finished with returncode=%s", proc.returncode)


@app.route("/api/scrape/start", methods=["POST"])
def api_scrape_start():
    """Launch run_local --headless in a background subprocess."""
    with _scrape_lock:
        if _scrape_job["status"] in ("running", "stopping"):
            return jsonify({"error": "Scraper is already running"}), 409

        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "src.local.run_local", "--headless"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                cwd=_PROJECT_ROOT,
                creationflags=creationflags,
            )
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _scrape_job.update(
                {
                    "status": "running",
                    "log": [],
                    "returncode": None,
                    "proc": proc,
                    "pid": proc.pid,
                    "started_at": now,
                    "finished_at": None,
                    "stop_requested": False,
                    "timed_out": False,
                }
            )
            threading.Thread(
                target=_reader_thread_fn, args=(proc, _scrape_job), daemon=True
            ).start()
            threading.Thread(
                target=_monitor_thread_fn, args=(proc, _scrape_job), daemon=True
            ).start()
            logger.info("Scraper started (pid=%s)", proc.pid)
            return jsonify({"ok": True, "pid": proc.pid})
        except Exception as e:
            logger.exception("Failed to start scraper")
            _scrape_job["status"] = "error"
            return jsonify({"error": str(e)}), 500


@app.route("/api/scrape/stop", methods=["POST"])
def api_scrape_stop():
    """Stop the running scraper: kill full process tree."""
    with _scrape_lock:
        proc = _scrape_job.get("proc")
        if _scrape_job["status"] != "running" or proc is None:
            return jsonify({"error": "Scraper is not running"}), 409
        _scrape_job["stop_requested"] = True
        _scrape_job["status"] = "stopping"
        _scrape_job["log"].append("[web] Stop requested by user...")

    _terminate_process_tree(proc)
    logger.info("Stop requested for scraper (pid=%s)", proc.pid)
    return jsonify({"ok": True})


@app.route("/api/scrape/status", methods=["GET"])
def api_scrape_status():
    """Return current scraper status + last 30 log lines + ETA info."""
    with _scrape_lock:
        response = {
            "status": _scrape_job["status"],
            "returncode": _scrape_job["returncode"],
            "log": _scrape_job["log"][-30:],
            "pid": _scrape_job.get("pid"),
            "started_at": _scrape_job.get("started_at"),
            "finished_at": _scrape_job.get("finished_at"),
        }

        # ── Compute ETA / progress ──────────────────────────────────────
        progress = _scrape_job.get("progress_data", {})

        elapsed_seconds = None
        started_at = _scrape_job.get("started_at")
        if started_at:
            try:
                s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                elapsed_seconds = (datetime.utcnow() - s).total_seconds()
            except (ValueError, AttributeError):
                pass

        # Historical averages for ETA estimation
        estimated_total_seconds = None
        eta_seconds = None
        metrics = _load_json_retry(SCRAPE_METRICS_FILE, fallback=[])
        successful = [m for m in metrics if m.get("total_seconds")]
        if successful:
            estimated_total_seconds = sum(m["total_seconds"] for m in successful) / len(successful)
            if elapsed_seconds is not None:
                eta_seconds = max(0.0, estimated_total_seconds - elapsed_seconds)

        current_search = progress.get("search_name")
        current_index = progress.get("index")
        total = progress.get("total")

        response.update(
            {
                "elapsed_seconds": round(elapsed_seconds, 1) if elapsed_seconds is not None else None,
                "estimated_total_seconds": round(estimated_total_seconds, 1) if estimated_total_seconds is not None else None,
                "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
                "progress": f"{current_index} / {total}" if (current_index is not None and total is not None) else None,
                "current_search": current_search,
            }
        )

        return jsonify(response)


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
