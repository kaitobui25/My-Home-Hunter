
---

## Bug A — `_drain_proc()` can block forever on stdout

### Problem

Current code does roughly:

```python
for line in proc.stdout:
    job["log"].append(...)
proc.wait()
job["status"] = "done" or "error"
````

If Playwright/Chromium child process keeps stdout pipe open, the loop may never end. Then `/api/scrape/status` remains `running` forever.

### Fix checklist

* [ ] Refactor scraper process handling in `src/web/app.py`.
* [ ] Split log reading and process monitoring into separate responsibilities:

  * [ ] one function/thread reads stdout lines and appends to job log;
  * [ ] one monitor function/thread waits for process completion.
* [ ] Do not depend on stdout EOF before setting final status.
* [ ] Ensure final status is set when `proc.wait()` returns:

  * returncode `0` → `done`
  * non-zero → `error`
  * user stopped → `stopped`
  * timeout killed → `timeout` or `error`
* [ ] Cap stored log lines to avoid memory growth, e.g. keep last 300–1000 lines.
* [ ] Start subprocess with unbuffered output:

  * use `sys.executable, "-u", "-m", "src.local.run_local", "--headless"`
  * or set env `PYTHONUNBUFFERED=1`
* [ ] Use `bufsize=1`, `text=True`, `stderr=subprocess.STDOUT`.

---

## Bug B — no total timeout / watchdog

### Problem

If scraper, Playwright, WAF, geocoder, or school scraper hangs, backend has no max runtime. UI can show running forever.

### Fix checklist

* [ ] Add configurable total timeout for scraper job.
* [ ] Suggested default: `SCRAPER_TIMEOUT_SECONDS = 15 * 60` or `20 * 60`.
* [ ] Ideally read from env:

  * `HOME_HUNTER_SCRAPER_TIMEOUT_SECONDS`
  * fallback to 900 or 1200 seconds.
* [ ] In monitor thread:

  * call `proc.wait(timeout=SCRAPER_TIMEOUT_SECONDS)`
  * on `subprocess.TimeoutExpired`, kill the whole process tree.
* [ ] Set job status after timeout:

  * `status = "timeout"` or `status = "error"`
  * `returncode = None` or actual returncode after kill
  * append log line like `[web] Scraper timed out after 900s. Process tree killed.`
* [ ] Frontend `_pollScrapeStatus()` must handle `timeout` status clearly.
* [ ] UI should reset button after timeout:

  * enable `btn-scrape`
  * hide stop button
  * show red/orange message: `✗ タイムアウト`

---

## Bug C — stop button does not kill process tree

### Problem

Current `/api/scrape/stop` only calls `proc.terminate()`. On Windows, Playwright/Chromium child processes may survive. The Python parent may die, but Chromium children can keep resources/pipes open.

### Fix checklist

* [ ] Implement process-tree kill helper in `src/web/app.py`.
* [ ] Prefer adding `psutil`:

  * [ ] add `psutil>=5.9.0` to `requirements.txt`
  * [ ] implement `_terminate_process_tree(proc, grace_seconds=5)`
* [ ] Kill logic:

  * [ ] get parent by `psutil.Process(proc.pid)`
  * [ ] collect children recursively
  * [ ] terminate children + parent
  * [ ] wait up to grace period
  * [ ] kill remaining children + parent
* [ ] If psutil is not available, provide fallback:

  * Windows: `taskkill /F /T /PID <pid>`
  * POSIX: `os.killpg(...)` if process group/session was created
* [ ] In `Popen`, create process group/session properly:

  * Windows: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` if available
  * POSIX: `start_new_session=True`
* [ ] `/api/scrape/stop` should:

  * set status to `stopping`
  * append `[web] Stop requested by user...`
  * call process-tree terminate
  * monitor thread should later set final status `stopped`
* [ ] Stop endpoint should not leave UI stuck in `stopping`.

---

## Job state robustness

* [ ] Protect all `_scrape_job` updates with `_scrape_lock`.
* [ ] Add fields if useful:

  * `started_at`
  * `finished_at`
  * `pid`
  * `timed_out`
  * `stop_requested`
* [ ] `/api/scrape/status` should return:

  * `status`
  * `returncode`
  * `log`
  * `pid`
  * `started_at`
  * `finished_at`
* [ ] Prevent double-start:

  * if status is `running` or `stopping`, return HTTP 409.
* [ ] If old job status is `done/error/stopped/timeout`, allow a new run.

---

## Frontend checklist: `src/web/templates/map.html`

* [ ] `_pollScrapeStatus()` currently handles `running`, `stopping`, `done`, `stopped`, else error.
* [ ] Add explicit handling for:

  * `timeout`
  * maybe `error`
* [ ] On every terminal status, always:

  * `clearInterval(_pollTimer)`
  * `_pollTimer = null`
  * `_liveRefreshTick = 0`
  * `btn.disabled = false`
  * `btn.textContent = "▶ データ取得実行"`
  * `stopBtn.style.display = "none"`
  * `stopBtn.disabled = false`
* [ ] On `done`, call `await loadData()`.
* [ ] On `error`, optionally call `await loadData({ silent: true })` only if useful.
* [ ] On `timeout`, do not keep spinner/running text.
* [ ] If `/api/scrape/status` request fails repeatedly, avoid infinite silent failure:

  * count consecutive failures;
  * after e.g. 5 failures, stop polling and show communication error.

---

## Manual test cases

### Test 1 — normal completion

* [ ] Run map server:

  ```bash
  python -m src.web.app
  ```
* [ ] Open map.
* [ ] Click `データ取得実行`.
* [ ] Confirm status becomes running.
* [ ] Confirm eventually status becomes:

  * `完了 — 新着 N件`
  * or `完了 (新規なし)`
* [ ] Confirm button is enabled again.

### Test 2 — stop button

* [ ] Click `データ取得実行`.
* [ ] Wait until running.
* [ ] Click `停止`.
* [ ] Confirm UI becomes `停止しました`.
* [ ] Confirm button is enabled again.
* [ ] Confirm no orphan Playwright/Chromium processes remain.

Windows check:

```powershell
Get-Process chrome, chromium, msedge, python -ErrorAction SilentlyContinue
```

### Test 3 — forced timeout

* [ ] Temporarily set timeout low, e.g. 5–10 seconds.
* [ ] Click `データ取得実行`.
* [ ] Confirm backend kills process tree.
* [ ] Confirm status becomes timeout/error, not infinite running.
* [ ] Confirm UI button resets.

### Test 4 — scraper crash

* [ ] Temporarily force `src.local.run_local` to exit non-zero.
* [ ] Click button.
* [ ] Confirm status becomes error.
* [ ] Confirm frontend shows returncode.
* [ ] Confirm button resets.

### Test 5 — stdout pipe safety

* [ ] Run a subprocess that prints logs slowly or keeps child process open.
* [ ] Confirm monitor still detects timeout/completion and does not wait forever on stdout EOF.

---

## Acceptance criteria

* [ ] `データ取得実行` cannot stay running forever without timeout.
* [ ] Backend always transitions from `running` to one terminal state:

  * `done`
  * `error`
  * `stopped`
  * `timeout`
* [ ] Stop button kills Python + Playwright/Chromium children.
* [ ] Frontend always resets button on terminal status.
* [ ] Logs still show in UI while scraper is running.
* [ ] No regression to `/api/listings`, `/api/schools`, `/api/ninkagai`.
* [ ] Run:

  ```bash
  python -m compileall src run.py
  ```
* [ ] If requirements changed:

  ```bash
  pip install -r requirements.txt
  ```

```

Điểm quan trọng nhất để agent không fix nửa vời: **phải có process-tree kill + timeout tổng + không đọc stdout kiểu block status**. Ba cái này đi chung mới hết bệnh “chạy mãi không dừng”.
```
