Here’s the checklist version:

````markdown
# Checklist: Fix Scraper Freeze / Silent Phase Issue

## 1. Add phase progress support

- [ ] Add `_emit_phase()` helper in `src/local/run_local.py`
- [ ] Include fields:
  - [ ] `phase`
  - [ ] `search_name`
  - [ ] `message`
  - [ ] extra progress data
- [ ] Log each phase with `logger.info()`

## 2. Emit phase when scraping starts

- [ ] Emit phase `scraping` before `hunter.scrape()`
- [ ] Include current search name
- [ ] Include site name if available

## 3. Emit phase when listing filtering starts

- [ ] Emit phase `filtering_listings`
- [ ] Include:
  - [ ] `total_listings`
  - [ ] `processed=0`
  - [ ] `matched=0`

## 4. Keep current listing progress

- [ ] Keep progress every 50 listings
- [ ] Keep counters:
  - [ ] `filtered`
  - [ ] `distance_filtered`
  - [ ] `deduped`
  - [ ] `geocode_failed`
  - [ ] `no_address`
- [ ] Confirm progress still reaches `1453 / 1453`

## 5. Emit final filter completion event

- [ ] After listing loop, emit phase `filter_done`
- [ ] Include final counters:
  - [ ] `processed=total_candidates`
  - [ ] `total_listings=total_candidates`
  - [ ] `matched`
  - [ ] `filtered`
  - [ ] `distance_filtered`
  - [ ] `deduped`
  - [ ] `geocode_failed`
  - [ ] `no_address`

## 6. Wrap final DB save with phase progress

- [ ] Emit phase `saving_seen_db` before final save
- [ ] Emit phase `saved_seen_db` after final save
- [ ] Add timing log around `_save_local_seen()`
- [ ] Log save duration with `INFO`, not `DEBUG`

## 7. Reduce heavy save frequency

- [ ] Replace hard-coded save every 50 listings
- [ ] Add constants:
  - [ ] `SAVE_EVERY_LISTINGS = 500`
  - [ ] `SAVE_EVERY_SECONDS = 15`
- [ ] Save when either threshold is reached
- [ ] Keep one final save after the loop

## 8. Add heartbeat for long phases

- [ ] Add heartbeat progress support
- [ ] Emit heartbeat during:
  - [ ] `saving_seen_db`
  - [ ] `notifying_telegram`
  - [ ] `delist_pass`
  - [ ] `school_search`
- [ ] Include message like `Still working...`

## 9. Wrap Telegram notification phase

- [ ] Emit phase `notifying_telegram` before `telegram.send_batch()`
- [ ] Emit phase `telegram_done` after sending
- [ ] Include:
  - [ ] matched count
  - [ ] sent count

## 10. Wrap search completion

- [ ] Emit phase `search_done`
- [ ] Include:
  - [ ] search name
  - [ ] elapsed seconds
  - [ ] matched count

## 11. Wrap delist pass

- [ ] Emit phase `delist_pass` before delist loop
- [ ] Emit phase `delist_done` after delist loop
- [ ] Include delisted count
- [ ] Save DB after delist with visible phase

## 12. Wrap school search

- [ ] Emit phase `school_search` before school vacancy scraping
- [ ] Emit phase `school_search_done` after it finishes
- [ ] Emit phase `school_search_error` if it fails
- [ ] Consider skipping school search unless explicitly enabled

## 13. Update web status API

File: `src/web/app.py`

- [ ] Add these fields to `/api/scrape/status` response:
  - [ ] `phase`
  - [ ] `phase_message`
  - [ ] `heartbeat`
- [ ] Keep existing listing progress fields unchanged

## 14. Update frontend UI

File: `src/web/templates/map.html`

- [ ] Update `renderScrapeEta(data)`
- [ ] Display current phase
- [ ] Display phase message
- [ ] Example output:

```text
Listings: 1453 / 1453 | Phase: saving_seen_db | Saving local seen DB
````

## 15. Final expected behavior

* [ ] UI no longer freezes visually after listing reaches 100%
* [ ] User can see current phase after filtering
* [ ] If DB save is slow, UI shows `saving_seen_db`
* [ ] If delist is running, UI shows `delist_pass`
* [ ] If school search is running, UI shows `school_search`
* [ ] If scraper is truly stuck, the stuck phase is visible

## 16. Test cases

* [ ] Run normal scrape from web UI
* [ ] Confirm progress reaches `Listings: total / total`
* [ ] Confirm UI changes to `filter_done`
* [ ] Confirm UI changes to `saving_seen_db`
* [ ] Confirm UI changes to `search_done`
* [ ] Confirm UI changes to `run_done`
* [ ] Confirm button re-enables after done
* [ ] Confirm no duplicate Telegram messages
* [ ] Confirm `local_seen_listings.json` is saved correctly
* [ ] Confirm stop button still kills process
* [ ] Confirm timeout still works

```
```
