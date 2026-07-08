# Task: Add Scraper ETA Panel to Web UI

Repository: `kaitobui25/My-Home-Hunter`

## Goal

Add a small ETA/progress panel on the web map page when the user clicks:

```text
▶ データ取得実行
```

The panel should show:

```text
⏱ 経過: 01:24
⌛ 残り予想: 約 03:10 / 学習中
📊 進捗: 2 / 3 searches
🔍 現在: Nifty Toyonaka Rental
```

Do not use a hardcoded ETA. Use elapsed time, previous successful run metrics, and structured progress emitted by the local runner.

Keep the change focused only on scraper ETA/progress. Do not refactor unrelated scraper/filter/map/notification logic.

---

## Files to update

* `src/web/app.py`
* `src/local/run_local.py`
* `src/web/templates/map.html`

Optional:

* `results-local/scrape_metrics.json` should be created automatically at runtime. Do not require it to exist beforehand.

---

## Backend: `src/web/app.py`

Add persistent metrics file:

```text
results-local/scrape_metrics.json
```

Store successful scrape durations only:

```json
{
  "total_runs": [
    {
      "started_at": "2026-07-08T12:00:00Z",
      "finished_at": "2026-07-08T12:05:12Z",
      "total_seconds": 312.4,
      "status": "done"
    }
  ],
  "search_runs": {
    "Toyonaka Rental": [
      {
        "seconds": 45.2,
        "site": "suumo",
        "finished_at": "2026-07-08T12:00:45Z"
      }
    ]
  }
}
```

Requirements:

* Add helpers:

  * `_load_scrape_metrics()`
  * `_save_scrape_metrics(metrics)`
  * `_record_scrape_run(job)`
  * `_average_total_seconds(metrics, max_runs=10)`
  * optionally `_average_search_seconds(metrics, search_name, max_runs=10)`
* Record only successful `done` runs.
* Do not use `error`, `timeout`, or `stopped` runs for ETA.
* Keep only latest 20–50 total runs.
* Keep only latest 20–50 records per search.
* In `/api/scrape/status`, return:

  * `elapsed_seconds`
  * `estimated_total_seconds`
  * `eta_seconds`
  * `progress`

ETA logic:

```text
elapsed_seconds = now - started_at

If per-search averages are available:
  eta = estimated remaining time of current search
        + average durations of remaining searches

Else if total-run average is available:
  eta = average_total_seconds - elapsed_seconds

Else:
  eta = null
```

If ETA is unknown, return:

```json
{
  "estimated_total_seconds": null,
  "eta_seconds": null
}
```

---

## Parse structured progress from subprocess logs

The backend already reads stdout from:

```text
python -u -m src.local.run_local --headless
```

Extend the stdout reader:

* If a line starts with:

```text
HH_PROGRESS 
```

parse the remaining text as JSON and update:

```python
_scrape_job["progress"]
```

Suggested progress object:

```json
{
  "current_search": "Nifty Toyonaka Rental",
  "current_site": "nifty",
  "search_index": 2,
  "search_total": 3,
  "completed_searches": 1,
  "current_search_elapsed_seconds": 42.3,
  "last_event": "search_start"
}
```

Do not let invalid JSON crash the reader.

---

## Local runner: `src/local/run_local.py`

Add helper:

```python
def _emit_progress(**data):
    print("HH_PROGRESS " + json.dumps(data, ensure_ascii=False), flush=True)
```

In `run_all()`, emit progress events around the active search loop.

Before all searches:

```python
_emit_progress(event="run_start", total=len(active_searches))
```

Before each search:

```python
_emit_progress(
    event="search_start",
    search=search.name,
    site=search.site or "suumo",
    index=i + 1,
    total=len(active_searches),
    completed=i,
)
```

After each search:

```python
_emit_progress(
    event="search_done",
    search=search.name,
    site=search.site or "suumo",
    index=i + 1,
    total=len(active_searches),
    completed=i + 1,
    seconds=round(time.perf_counter() - search_start, 2),
)
```

After all searches:

```python
_emit_progress(event="run_done", total=len(active_searches))
```

Use `time.perf_counter()` for durations.

---

## Frontend: `src/web/templates/map.html`

Add ETA block under `scrape-status`:

```html
<div id="scrape-eta"></div>
```

Suggested CSS:

```css
#scrape-eta {
    margin-top: 6px;
    padding: 7px 8px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: rgba(0, 0, 0, 0.25);
    font-size: 10.5px;
    color: var(--text-muted);
    line-height: 1.5;
    display: none;
}
```

Add JS helper:

```js
function fmtDuration(sec) {
    if (sec === null || sec === undefined || sec < 0) return "—";
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;

    if (h > 0) {
        return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }
    return `${m}:${String(s).padStart(2, "0")}`;
}
```

Add render function:

```js
function renderScrapeEta(data) {
    const el = document.getElementById("scrape-eta");
    if (!el) return;

    if (!["running", "stopping"].includes(data.status)) {
        el.style.display = "none";
        el.innerHTML = "";
        return;
    }

    el.style.display = "block";

    const elapsed = fmtDuration(data.elapsed_seconds);
    const eta =
        data.eta_seconds === null || data.eta_seconds === undefined
            ? "学習中"
            : "約 " + fmtDuration(data.eta_seconds);

    const p = data.progress || {};
    const progressText =
        p.search_index && p.search_total
            ? `${p.search_index} / ${p.search_total}`
            : "—";

    const current = p.current_search || "準備中";

    el.innerHTML = `
        <div>⏱ 経過: ${elapsed}</div>
        <div>⌛ 残り予想: ${eta}</div>
        <div>📊 進捗: ${progressText}</div>
        <div>🔍 現在: ${current}</div>
    `;
}
```

Call `renderScrapeEta(data)` inside `_pollScrapeStatus()` after fetching `/api/scrape/status`.

When scrape starts, show the panel immediately if possible.

On terminal states:

```text
done
error
stopped
timeout
```

hide the ETA panel or leave final elapsed time briefly. Prefer hiding it to keep UI clean.

---

## Acceptance Criteria

* ETA/progress panel appears after clicking `データ取得実行`.
* Elapsed time updates while running.
* Current search name is shown.
* Search progress is shown, e.g. `1 / 3`, `2 / 3`, `3 / 3`.
* If no historical data exists, ETA shows `学習中`.
* After at least one successful run, future runs show estimated remaining time.
* `/api/scrape/status` returns:

  * `elapsed_seconds`
  * `estimated_total_seconds`
  * `eta_seconds`
  * `progress`
* Successful runs are saved to `results-local/scrape_metrics.json`.
* Failed/stopped/timeout runs are not used for ETA average.
* Existing start/stop/status behavior still works.
* Existing live logs still work.
* Run:

```bash
python -m compileall src run.py
```
