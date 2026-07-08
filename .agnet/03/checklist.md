Here is a clean English checklist for the agent. Root cause is around `src/local/run_local.py`: it processes all candidate listings silently after the `Processing ... listings` log, and currently saves the full local DB during the per-listing loop.

```text
# Debug / Fix Checklist: Local Scraper Appears Stuck During Listing Processing

## Goal

Fix the issue where the local scraper appears to freeze after this log:

[Toyonaka Rental] Processing 1453 listings (geocode/filter/notify)...

The web elapsed timer continues running, but the log does not show progress. The user needs clear progress visibility and better performance during geocode/filter/notify.

## Files to inspect

- src/local/run_local.py
- src/geocoder.py
- src/web/app.py
- src/web/templates/map.html

## Root cause hypotheses

- The scraper is not actually frozen; it is processing many listings with almost no INFO-level progress logs.
- The loop processes every candidate listing one by one.
- For each listing, it may call geocoder.get_coordinates().
- For each listing, it currently calls on_seen_updated(seen), which writes the full local_seen_listings.json file repeatedly.
- If there are 1453 listings, this can result in 1453 full JSON writes.
- Geocoding may also be slow when many addresses are not cached.
- The web timer only shows elapsed time from started_at. It does not prove that listing processing is making progress.

## Required fixes

### 1. Add per-listing progress logging

In src/local/run_local.py, inside run_local_search(), add progress logging during the candidate_listings loop.

Recommended behavior:

- Log every 25 or 50 listings.
- Always log the final listing.
- Include:
  - search name
  - processed count
  - total count
  - matched count
  - current listing name or address if useful
  - elapsed seconds for this processing phase

Example log format:

[{}] Progress: {}/{} processed | matched={} | elapsed={}s

### 2. Emit HH_PROGRESS for listing-level progress

The web UI already parses HH_PROGRESS lines in src/web/app.py.

Add _emit_progress() calls during the listing loop.

Suggested payload:

{
  "listing_progress": true,
  "search_name": search.name,
  "processed": idx,
  "total_listings": total,
  "matched": len(matched)
}

This allows /api/scrape/status to expose real listing progress, not just search-level progress.

### 3. Stop saving local_seen_listings.json on every listing

Currently run_all() passes on_seen_updated=_save_local_seen into run_local_search().

Inside the listing loop, this causes the full DB to be saved after each listing.

Change this behavior to batch saving.

Recommended behavior:

- Save every 50 processed listings.
- Save once at the end of run_local_search().
- Keep atomic save logic unchanged.
- Do not lose data if the process crashes after a large batch.

Example logic:

if idx % 50 == 0:
    _save_local_seen(seen)

At the end:

_save_local_seen(seen)

### 4. Avoid double-saving too aggressively

Check all places where _save_local_seen(seen) is called:

- inside run_local_search()
- in run_all() after each search
- after delist pass
- after reset/refilter

Keep enough saves for safety, but remove unnecessary repeated saves.

Target behavior:

- During listing loop: batch save only
- After one search completes: save once
- After delist pass: save once if needed

### 5. Add geocode progress visibility

In src/geocoder.py, get_coordinates() already logs when a new address is geocoded.

Improve visibility from run_local.py:

- Count cache hits vs new geocode requests if possible.
- Log when a listing has no address.
- Log geocode failures only as warning/error.
- Do not spam every listing unless debug mode is enabled.

### 6. Add slow-listing detection

Inside the listing loop, measure time per listing.

If one listing takes longer than 5 seconds, log a warning:

Slow listing processing: {seconds}s | {name} | {address} | {url}

This will reveal whether the freeze is caused by geocoding, file writing, filtering, or Telegram.

### 7. Update web scrape status to show listing progress

In src/web/app.py, _try_parse_progress() already stores progress_data.

Ensure listing_progress data is preserved.

In /api/scrape/status, include:

- processed
- total_listings
- matched
- listing_progress_text

Example:

"listing_progress": "350 / 1453"
"processed_listings": 350
"total_listings": 1453
"matched_listings": 12

### 8. Update frontend scrape status display

In src/web/templates/map.html, renderScrapeEta(data) currently displays elapsed time, ETA, search progress, and current search.

Add listing progress display if available:

Properties:
- data.listing_progress
- data.processed_listings
- data.total_listings
- data.matched_listings

Example display:

🏠 Listings: 350 / 1453 | matched: 12

### 9. Fix datetime.utcnow() deprecation warning

Replace:

datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")

with timezone-aware UTC:

from datetime import datetime, timezone

datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

Apply this in src/local/run_local.py anywhere utcnow() is used.

### 10. Keep behavior unchanged

Do not change filtering logic.

Do not change Telegram message format.

Do not change listing matching criteria.

Do not change geocoder result format.

Do not change local_seen_listings.json schema except normal existing fields.

The fix should only improve:
- progress visibility
- performance
- logging
- deprecation warning

## Acceptance criteria

- When processing 1453 listings, the terminal log updates at least every 50 listings.
- Web UI shows listing-level progress, not only elapsed time.
- local_seen_listings.json is not rewritten once per listing.
- Scraper still saves data safely during long runs.
- The process no longer appears stuck after "Processing ... listings".
- DeprecationWarning for datetime.utcnow() is gone.
- Existing map rendering still works.
- Existing Telegram notification behavior still works.
```
