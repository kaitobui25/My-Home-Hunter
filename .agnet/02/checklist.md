Bản gọn:

````md
# Checklist: Add Scraper ETA Panel

Repo: kaitobui25/My-Home-Hunter

## Backend: src/web/app.py

- [ ] Add metrics file: results-local/scrape_metrics.json
- [ ] Save only successful runs: status == "done"
- [ ] Do not use error / stopped / timeout runs for ETA
- [ ] Store:
  - started_at
  - finished_at
  - total_seconds
  - per-search seconds if available
- [ ] Add ETA fields to /api/scrape/status:
  - elapsed_seconds
  - estimated_total_seconds
  - eta_seconds
  - progress
- [ ] If no history, return eta_seconds = null
- [ ] Clamp ETA so it never becomes negative
- [ ] Parse stdout lines starting with:
  - HH_PROGRESS 
- [ ] Invalid HH_PROGRESS JSON must not crash backend

## Local runner: src/local/run_local.py

- [ ] Add helper:

```python
def _emit_progress(**data):
    print("HH_PROGRESS " + json.dumps(data, ensure_ascii=False), flush=True)
````

* [ ] Emit before all searches:

  * run_start
* [ ] Emit before each search:

  * search_start
  * search name
  * site
  * index
  * total
* [ ] Emit after each search:

  * search_done
  * seconds
* [ ] Emit after all searches:

  * run_done
* [ ] Use time.perf_counter()

## Frontend: src/web/templates/map.html

* [ ] Add under scrape-status:

```html
<div id="scrape-eta"></div>
```

* [ ] Add small CSS for #scrape-eta
* [ ] Add fmtDuration(sec)
* [ ] Add renderScrapeEta(data)
* [ ] Show while status is:

  * running
  * stopping
* [ ] Hide when status is:

  * done
  * error
  * stopped
  * timeout
* [ ] Display:

  * elapsed time
  * ETA or 学習中
  * progress 1 / 3
  * current search name

## Tests

* [ ] First run with no metrics → ETA shows 学習中
* [ ] After successful run → metrics file created
* [ ] Second run → ETA appears
* [ ] Stop button → ETA panel resets
* [ ] Timeout/error → ETA panel does not get stuck
* [ ] Existing live logs still work
* [ ] Existing start/stop/status still work

## Command

```bash
python -m compileall src run.py
```

```
```
