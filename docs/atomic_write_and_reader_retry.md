Atomic write + JSON read retry
===============================

Date: 2026-05-23

Problem
-------
When the local scraper (src/local/run_local.py) writes results-local/local_seen_listings.json at the same time the web server (src/web/app.py) reads it, json.load() could see a partially-written file and raise JSONDecodeError. On Windows, os.replace() can also fail transiently due to file locking.

What was changed
----------------
1. src/local/run_local.py
   - Use atomic write: write to a temp file in the same directory, call f.flush() and os.fsync(), then atomically replace the target file.
   - Add a small replace-retry helper (_atomic_replace_with_retry) that retries os.replace() on PermissionError/OSError with exponential backoff (helps on Windows file-lock races).

2. src/web/app.py
   - Add _load_json_retry(path, attempts=3, delay=0.1, fallback) which retries on JSONDecodeError (exponential backoff) and returns a safe fallback on persistent failure.
   - Use _load_json_retry when reading: results-local/local_seen_listings.json, results/geocode_cache.json, and the school data files.
   - Add a short sleep-based backoff (time) for retries.

Why this helps
---------------
- Atomic write + fsync ensures readers never observe a truncated file: either the old file or the new complete file.
- Replace-retry reduces commit failures on Windows when another process briefly holds the file open.
- Reader retry reduces the chance the frontend returns HTTP 500 due to transient partial writes.

Files modified
--------------
- src/local/run_local.py
- src/web/app.py
- docs/atomic_write_and_reader_retry.md

How to test
-----------
1. Start the web server: python -m src.web.app
2. Run the local scraper: python -m src.local.run_local
3. While the scraper runs, repeatedly call: curl http://localhost:5001/api/listings
4. Confirm no JSONDecodeError in web logs; API should return 200 and an empty or filled listings object depending on data.

Notes / next steps
------------------
- Could add file-level locking for stricter semantics (portalocker), but current approach minimizes complexity and is cross-platform.
- Consider keeping a .bak copy before replace for post-mortem if corruption still occurs.

Committed on branch: atomic-write/local-seen
