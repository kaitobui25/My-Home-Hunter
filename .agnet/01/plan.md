# Task: Fix scraper button runs forever — bugs A/B/C

Repo: kaitobui25/My-Home-Hunter

## Goal

Fix issue where pressing `▶ データ取得実行` keeps running forever and UI never returns to done/error/stopped.

Main files:
- `src/web/app.py`
- `src/web/templates/map.html`
- `requirements.txt` if adding `psutil`

Context:
- Frontend button `データ取得実行` calls `startScrape()`.
- `startScrape()` POSTs `/api/scrape/start`.
- Backend starts subprocess: `python -m src.local.run_local --headless`.
- Frontend polls `/api/scrape/status`.
- Current backend only sets status done/error after stdout loop ends.
- Current stop only calls `proc.terminate()` and may not kill Playwright/Chromium child processes.

---

## Bug A — `_drain_proc()` can block forever on stdout

### Problem

Current code does roughly:

```python
for line in proc.stdout:
    job["log"].append(...)
proc.wait()
job["status"] = "done" or "error"