"""Optimized Home Hunter web entry point.

It layers small, isolated improvements over the existing Flask app:
- launches the progressive scraper runner;
- caches /api/listings until its backing files change;
- sorts API results nearest-first;
- persists map user state (favorites/viewed) on local disk;
- injects the small map UX patch without forking the large template.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from flask import jsonify, request

from src.web import app as legacy

app = legacy.app

_LISTINGS_CACHE_LOCK = threading.Lock()
_LISTINGS_CACHE_KEY = None
_LISTINGS_CACHE_PAYLOAD = None

_MAP_STATE_LOCK = threading.Lock()
MAP_STATE_FILE = os.path.join(
    legacy._PROJECT_ROOT, "results-local", "map_user_state.json"
)


def _mtime_ns(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return -1


def _listings_source_key() -> tuple[int, int, int]:
    return (
        _mtime_ns(legacy.LOCAL_SEEN_FILE),
        _mtime_ns(legacy.GEOCODE_CACHE_FILE),
        _mtime_ns(legacy.CONFIG_FILE),
    )


def _clean_state_ids(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, str) and value})


def _load_map_state() -> dict:
    try:
        with open(MAP_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"favorites": [], "viewed": []}
    except (OSError, json.JSONDecodeError) as exc:
        legacy.logger.warning("Could not read map user state: %s", exc)
        return {"favorites": [], "viewed": []}

    return {
        "favorites": _clean_state_ids(data.get("favorites")),
        "viewed": _clean_state_ids(data.get("viewed")),
    }


def _save_map_state(state: dict) -> None:
    os.makedirs(os.path.dirname(MAP_STATE_FILE), exist_ok=True)
    temp_file = MAP_STATE_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, MAP_STATE_FILE)


@app.route("/api/map-state", methods=["GET", "PUT"])
def api_map_state():
    """Load or replace local map favorites/viewed state."""
    if request.method == "GET":
        with _MAP_STATE_LOCK:
            return jsonify(_load_map_state())

    payload = request.get_json(silent=True) or {}
    state = {
        "favorites": _clean_state_ids(payload.get("favorites")),
        "viewed": _clean_state_ids(payload.get("viewed")),
    }
    try:
        with _MAP_STATE_LOCK:
            _save_map_state(state)
    except OSError as exc:
        legacy.logger.error("Could not save map user state: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify(state)


def api_listings_v2():
    """Return a cached, nearest-first version of the existing listings API."""
    global _LISTINGS_CACHE_KEY, _LISTINGS_CACHE_PAYLOAD
    key = _listings_source_key()
    with _LISTINGS_CACHE_LOCK:
        if key == _LISTINGS_CACHE_KEY and _LISTINGS_CACHE_PAYLOAD is not None:
            return jsonify(_LISTINGS_CACHE_PAYLOAD)

    response = app.make_response(legacy.api_listings())
    if response.status_code != 200:
        return response

    payload = response.get_json(silent=True) or {}
    listings = payload.get("listings") or []
    listings.sort(
        key=lambda item: (
            item.get("distance_km") is None,
            item.get("distance_km")
            if item.get("distance_km") is not None
            else float("inf"),
            item.get("first_seen_at") or "",
        )
    )
    payload["listings"] = listings

    with _LISTINGS_CACHE_LOCK:
        _LISTINGS_CACHE_KEY = key
        _LISTINGS_CACHE_PAYLOAD = payload
    return jsonify(payload)


def api_scrape_start_v2():
    """Launch the progressive runner while reusing the existing job monitor."""
    with legacy._scrape_lock:
        if legacy._scrape_job["status"] in ("running", "stopping"):
            return jsonify({"error": "Scraper is already running"}), 409

        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "src.local.run_local_v2",
                    "--headless",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=legacy._PROJECT_ROOT,
                creationflags=creationflags,
            )
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            legacy._scrape_job.update(
                {
                    "status": "running",
                    "log": [f"[web] Progressive scraper started pid={proc.pid}"],
                    "returncode": None,
                    "proc": proc,
                    "pid": proc.pid,
                    "started_at": now,
                    "finished_at": None,
                    "stop_requested": False,
                    "timed_out": False,
                    "progress_data": {},
                    "search_times": [],
                    "reader_started": None,
                    "reader_alive": False,
                    "reader_first_line_at": None,
                    "reader_ended_at": None,
                    "reader_error": None,
                    "_reader_thread": None,
                    "_monitor_thread": None,
                }
            )
            reader_thread = threading.Thread(
                target=legacy._reader_thread_fn,
                args=(proc, legacy._scrape_job),
                daemon=True,
            )
            monitor_thread = threading.Thread(
                target=legacy._monitor_thread_fn,
                args=(proc, legacy._scrape_job),
                daemon=True,
            )
            reader_thread.start()
            monitor_thread.start()
            legacy._scrape_job["_reader_thread"] = reader_thread
            legacy._scrape_job["_monitor_thread"] = monitor_thread
            legacy.logger.info("Progressive scraper started (pid=%s)", proc.pid)
            return jsonify({"ok": True, "pid": proc.pid, "mode": "progressive"})
        except Exception as exc:
            legacy.logger.exception("Failed to start progressive scraper")
            legacy._scrape_job["status"] = "error"
            return jsonify({"error": str(exc)}), 500


app.view_functions["api_listings"] = api_listings_v2
app.view_functions["api_scrape_start"] = api_scrape_start_v2


@app.after_request
def inject_ux_patch(response):
    content_type = response.headers.get("Content-Type", "")
    if response.status_code == 200 and content_type.startswith("text/html"):
        html = response.get_data(as_text=True)
        script = '<script src="/static/homehunter-ux-v2.js"></script>'
        if script not in html and "</body>" in html:
            response.set_data(html.replace("</body>", script + "\n</body>"))
    return response


if __name__ == "__main__":
    print("\n" + "=" * 52)
    print("  HOME HUNTER — OPTIMIZED MAP VIEWER")
    print("=" * 52)
    print("  Popup : click only (hover = small tooltip)")
    print("  State : favorites/viewed saved to results-local/map_user_state.json")
    print("  API   : nearest-first + file-change cache")
    print("  URL   : http://localhost:5001")
    print("=" * 52 + "\n")
    app.run(host="0.0.0.0", port=5001, debug=False)
