## Plan: Fix scraper “stuck after listing progress” properly

### Goal

Make the scraper never look frozen after `Listings: 1453 / 1453`.
The UI should always show **what phase is running**, even after listing filtering is finished.

---

## 1. Add explicit scraper phases

Add a small helper in `src/local/run_local.py`:

```python
def _emit_phase(phase: str, search_name: str | None = None, message: str | None = None, **extra):
    payload = {
        "phase": phase,
        "search_name": search_name,
    }
    if message:
        payload["message"] = message
    payload.update(extra)
    _emit_progress(**payload)
    logger.info("[Phase] %s%s", phase, f" - {message}" if message else "")
```

Use phases like:

```text
run_start
scraping
filtering_listings
saving_seen_db
notifying_telegram
search_done
delist_pass
school_search
run_done
```

---

## 2. Emit a final `filter_done` progress event

Right after the listing loop finishes, emit a clear final event:

```python
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
```

This prevents the UI from sitting silently at `1453 / 1453`.

---

## 3. Wrap DB save with progress and timing

Before and after saving `local_seen_listings.json`:

```python
_emit_phase("saving_seen_db", search_name=search.name, message="Saving local seen DB")

if save_counter > 0 and callable(on_seen_updated):
    on_seen_updated(seen)

_emit_phase("saved_seen_db", search_name=search.name, message="Local seen DB saved")
```

Also update `_save_local_seen()` to log save duration:

```python
started = time.perf_counter()
...
elapsed = time.perf_counter() - started
logger.info(
    "[Local] Saved %d seen listings in %.1fs: %s",
    len(seen), elapsed, LOCAL_SEEN_FILE,
)
```

---

## 4. Reduce heavy save frequency

Current code saves after every 50 listings. That can be heavy because it rewrites the full JSON file every time.

Change to either:

```python
SAVE_EVERY_LISTINGS = 500
```

or better:

```python
SAVE_EVERY_LISTINGS = 500
SAVE_EVERY_SECONDS = 15
```

Then save only when either condition is met.

---

## 5. Add heartbeat during long phases

For phases that may take time, emit occasional heartbeat:

```python
_emit_progress(
    heartbeat=True,
    phase="saving_seen_db",
    search_name=search.name,
    message="Still saving local seen DB",
)
```

Use this around:

```text
saving_seen_db
delist_pass
school_search
telegram_send
```

---

## 6. Update `/api/scrape/status`

In `src/web/app.py`, include phase fields in the status response:

```python
"phase": progress.get("phase"),
"phase_message": progress.get("message"),
"heartbeat": progress.get("heartbeat"),
```

Current status already returns listing progress fields, so this is a small extension.

---

## 7. Update UI display

In `src/web/templates/map.html`, update `renderScrapeEta(data)`:

```js
if (data.phase) {
    parts.push("⚙️ Phase: " + data.phase);
}
if (data.phase_message) {
    parts.push(data.phase_message);
}
```

Then after `1453 / 1453`, the UI can show:

```text
Listings: 1453 / 1453 | Phase: saving_seen_db | Saving local seen DB
```

instead of looking frozen.

---

## 8. Optional but recommended: skip school search unless enabled intentionally

After normal housing scrape, `run_all()` also has a school vacancy section. If it runs silently, it can look like the housing scraper is stuck.

Add phase logs around it:

```python
_emit_phase("school_search", message="Running school vacancy searches")
```

Or add a config/CLI flag so housing scrape does not accidentally run school search.

---

## Final expected behavior

Before:

```text
Listings: 1453 / 1453
```

Then nothing, user thinks it is frozen.

After:

```text
Listings: 1453 / 1453 | Phase: filter_done
Phase: saving_seen_db
Phase: saved_seen_db
Phase: search_done
Phase: delist_pass
Phase: run_done
```

This fixes the real UX problem and also makes future freezes diagnosable.
