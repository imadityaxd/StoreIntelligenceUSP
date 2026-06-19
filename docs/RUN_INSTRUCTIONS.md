# Run Instructions

This runbook explains how to run the current USP-focused Store Intelligence
product locally.

The USP is:

> Upload or select CCTV footage, detect and track people, show person boxes and
> IDs on video, generate session-scoped retail intelligence, and export a
> business report.

This runbook is intentionally scoped to the finished USP: local CCTV analysis,
visible tracking, session metrics, history, and report export.

---

## 1. What The Product Does Right Now

The current USP flow can:

- Run an Analytics API.
- Run a live dashboard.
- Use saved CCTV clips already known to the project.
- Upload a new CCTV video from the dashboard.
- Create a new isolated session for each run.
- Run YOLO person detection.
- Track detected people with a camera-specific tracker profile.
- Draw person boxes and IDs over the video.
- Generate events such as entry, zone visits, billing queue joins, and queue
  abandonment when layout/camera role supports it.
- Show session-scoped metrics on the dashboard.
- Export the current session as Markdown, CSV, or JSON.
- Reopen recent sessions from Session History.

Important rule:

New analysis should be treated as a new `session_id`. The dashboard should show
current-session data by default, not silently mix old events into a new run.

---

## 2. Project Path

Use this path:

```powershell
cd D:\code\purplletech\projectXD
```

Saved CCTV footage is discovered under:

```text
StoreIntelligenceMedia\originals
```

The current project-local path is:

```text
D:\Code\StoreIntelligenceUSP-main\StoreIntelligenceMedia\originals
```

For an external media disk or directory, set the root before starting the
dashboard:

```powershell
$env:STORE_MEDIA_ROOT="D:\StoreIntelligenceMedia"
```

---

## 3. First-Time Setup

Run this once, or rerun if your virtual environment breaks.

```powershell
cd D:\code\purplletech\projectXD
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

---

## 4. Clean Database For Fresh Testing

Use this when you want a clean product test with no old demo events.

Stop any running API/dashboard terminals first.

### Option A: Use A Separate Clean DB File

This is the recommended approach because it does not delete old files.

In every API terminal, set:

```powershell
$env:STORE_DB_PATH="data\dashboard_live.sqlite3"
$env:STORE_AUTO_SEED_EVENTS="0"
$env:STORE_AUTO_LOAD_POS="0"
```

This tells the API:

- use `data\dashboard_live.sqlite3`
- do not auto-load demo events
- do not auto-load demo POS

### Option B: Delete The Clean Test DB

Only delete the clean test DB if you want a completely fresh run:

```powershell
Remove-Item -LiteralPath data\dashboard_live.sqlite3 -ErrorAction SilentlyContinue
```

Do not delete random files under `data\` unless you know what they contain.
Session artifacts and uploaded videos can live there.

### Option C: Check What Is In The DB

Run this after the API has created/used the DB:

```powershell
python -c "import sqlite3, pathlib; p=pathlib.Path('data/dashboard_live.sqlite3'); print('exists=', p.exists()); conn=sqlite3.connect(p); print('tables=', [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")]); print('sessions=', conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]); print('events=', conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]); print('pos=', conn.execute('SELECT COUNT(*) FROM pos_transactions').fetchone()[0])"
```

Expected clean start:

```text
sessions = 0
events = 0
pos = 0
```

After one dashboard run, sessions/events should increase.

---

## 5. Start The API

Open terminal 1:

```powershell
cd D:\code\purplletech\projectXD
.\.venv\Scripts\Activate.ps1

$env:STORE_DB_PATH="data\dashboard_live.sqlite3"
$env:STORE_AUTO_SEED_EVENTS="0"
$env:STORE_AUTO_LOAD_POS="0"

python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check API health:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

Expected:

```json
{"status":"OK","database":"ok", ...}
```

If the API fails:

- confirm the virtual environment is active
- confirm dependencies are installed
- confirm port `8000` is free
- check whether another API process is already running

---

## 6. Start The Dashboard

Open terminal 2:

```powershell
cd D:\code\purplletech\projectXD
.\.venv\Scripts\Activate.ps1

$env:STORE_API_BASE_URL="http://127.0.0.1:8000"

python -m uvicorn dashboard.server:app --reload --host 127.0.0.1 --port 8001
```

Open:

```text
http://127.0.0.1:8001
```

If you prefer the old browser port:

```powershell
python -m uvicorn dashboard.server:app --reload --host 127.0.0.1 --port 8121
```

Open:

```text
http://127.0.0.1:8121
```

---

## 7. Fastest Manual USP Test

Use a saved CCTV source first because it already has project context.

1. Open the dashboard.
2. In `Live Monitoring`, choose a camera source.
3. Good first choices:
   - `CAM_1` or `CAM_2` for zone activity.
   - `CAM_3` for entry/exit.
   - `CAM_5` for billing queue.
4. Keep `Show person boxes` enabled.
5. Click `Start Monitoring`.
6. Wait for the session to process.

For a lighter CPU run, open `ML/Ops Tuning` and use:

```text
Analysis window: 30
Process FPS: 2
Replay speed: 8
Tracker: Auto profile
YOLO confidence: 0.40
YOLO IoU: 0.28
```

What should happen:

- `Current Session` changes from idle to processing/replaying/complete.
- The video preview appears.
- Person boxes appear on the video.
- Person IDs are shown with boxes.
- Metrics begin updating.
- Event feed shows events for the current session.
- Funnel/heatmap update if that camera/layout supports them.
- Report buttons become active.

---

## 8. Testing With A New Uploaded Video

Use this to confirm the product is not only using hardcoded sample data.

1. Open dashboard.
2. Go to `Analyze New Video`.
3. Enter a session name.
4. Choose a CCTV video file.
5. Select:
   - Store: `STORE_BLR_002` if testing with Brigade Road-like footage.
   - Camera: choose the closest camera angle.
   - Camera role: `zone`, `entry`, `billing`, or `detection_only`.
   - Analysis: choose matching analysis mode.
6. Click `Start Analysis`.

Expected for a known/mapped camera:

- person boxes
- stable-ish IDs
- events
- metrics
- session report

Expected for an unknown camera angle:

- detection boxes and IDs should still work
- business events may be weak or detection-only
- zone/queue/funnel may be unavailable or low-confidence

This is correct behavior. The USP should not fake zone intelligence when the
camera has no calibration.

---

## 9. What Metrics To Look For

### Valid People

Meaning:

Number of unique physical people counted in the selected session.

How it is created:

- YOLO detects people.
- Tracker assigns IDs.
- A physical-person validation layer checks track stability, footpoint
  plausibility, box shape, upper-body/head evidence proxy, and perspective
  sanity.
- Events are grouped by person ID only after the track is countable.
- Reflection-like, tiny, unstable, or physically implausible tracks are marked
  suspect and excluded from metrics.

What to expect:

- For short 30-second clips, this number may be small.
- If tracking is unstable, people may be overcounted.
- If the physical-person filter is too strict, people may be undercounted.
- Suspect boxes may still be visible for review, but they should not create
  events or inflate the metric.

### Events

Meaning:

Number of structured business events generated in the current session.

Examples:

- `ENTRY`
- `EXIT`
- `REENTRY`
- `ZONE_ENTER`
- `ZONE_EXIT`
- `ZONE_DWELL`
- `BILLING_QUEUE_JOIN`
- `BILLING_QUEUE_ABANDON`

How it is created:

- Person tracks move through camera-specific zones or lines.
- The pipeline emits events when a person enters/exits a zone or crosses an
  entry line.

What to expect:

- Zone cameras generate zone events.
- Entry cameras generate entry/exit events.
- Billing cameras generate queue events.
- Detection-only uploads may generate fewer business events.

### Queue

Meaning:

Current or latest estimated queue depth.

How it is created:

- Billing camera detects valid physical people.
- People inside billing/queue zones are counted.
- Queue join/abandon events are emitted when track movement supports it.

What to expect:

- Best on `CAM_5` or a billing-role uploaded video.
- Weak if the billing zone is not calibrated.
- Mirror/reflection-like boxes should be suppressed by physical-person
  validation before they affect queue depth.

### Conversion

Meaning:

How many counted people are believed to have converted.

Two levels exist:

- Confirmed conversion: requires POS match.
- Estimated billing intent: based on billing queue/person behavior.

How it is created:

- POS transactions are matched to billing-related person tracks within a time window.
- If POS is missing, dashboard should not pretend conversion is confirmed.

What to expect:

- Without POS upload, conversion may be `estimated` or `0`.
- With POS, conversion becomes more trustworthy.

### Funnel

Meaning:

Person journey stages within the session.

Example:

```text
entry -> zone_visit -> billing_queue -> conversion
```

How it is created:

- Events are deduplicated by person/session.
- Each counted person contributes to stages they reached.

What to expect:

- Stronger when the selected camera/clip covers those stages.
- A single camera may not support the full journey.

### Heatmap

Meaning:

Which zones had the strongest activity.

How it is created:

- Zone enter/dwell events are aggregated by zone.
- Dwell time and visit count influence zone score.

What to expect:

- Works best on calibrated zone cameras.
- Weak or empty for detection-only uploads.

### Data Reliability

Meaning:

A quality score/grade that tells whether the output is strong enough to trust.

How it is created:

- Counts low-confidence events.
- Looks at inferred/adjusted events.
- Checks missing zone IDs for zone-related events.
- Computes average confidence.

What to expect:

- Short clips may have lower confidence.
- Unknown camera angles may have weaker reliability.
- Low reliability does not mean failure; it means the product is being honest.

---

## 10. Report Outputs

After a session starts, the `Current Session` card exposes:

- `Report`: Markdown report for human reading.
- `CSV`: spreadsheet-friendly export.
- `JSON`: structured export for integrations.

Report endpoint pattern:

```text
/dashboard/sessions/<session_id>/report?format=md
/dashboard/sessions/<session_id>/report?format=csv
/dashboard/sessions/<session_id>/report?format=json
```

The report should include:

- session ID
- store ID
- camera ID
- analysis mode
- valid people
- event count
- queue metrics
- conversion signal
- POS match info
- data reliability
- overlay availability
- funnel
- top zones
- alerts

Important:

Reports are scoped to the current session. They should not silently include old
events from previous runs.

---

## 11. Manual Test Checklist

Use this checklist after any major change.

### API

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
Invoke-WebRequest http://127.0.0.1:8000/sessions | Select-Object -ExpandProperty Content
```

Pass condition:

- health returns `OK`
- sessions endpoint responds without error

### Dashboard Load

Open:

```text
http://127.0.0.1:8001
```

Pass condition:

- page loads
- source dropdown has cameras
- no immediate red fatal error

### Saved CCTV Session

1. Select `CAM_5` billing source.
2. Set analysis window to `30`.
3. Set process FPS to `2`.
4. Click `Start Monitoring`.

Pass condition:

- session is created
- detection runs
- events appear
- report links work

### New Upload Session

1. Upload a CCTV video.
2. Choose store/camera/role.
3. Start analysis.

Pass condition:

- upload succeeds
- new session is created
- output is scoped to that session
- detection boxes appear if people are visible

### Session History

1. Complete one session.
2. Refresh browser.
3. Open `Session History`.
4. Reopen the session.

Pass condition:

- previous session is visible
- previous report can be downloaded
- metrics still belong to that session

### Report

Click:

- `Report`
- `CSV`
- `JSON`

Pass condition:

- files download
- session ID matches the active/reopened session
- data is understandable

---

## 12. Automated Verification

Run:

```powershell
cd D:\code\purplletech\projectXD
.\.venv\Scripts\Activate.ps1
python -m py_compile dashboard\server.py app\reports.py
python -m unittest discover -s tests -p "test_*.py"
```

Expected:

```text
OK
```

The current known-good result after USP completion was:

```text
Ran 80 tests
OK
```

The exact number can increase as tests are added.

---

## 13. Recommended Settings

### CPU-Friendly Demo

Use this when running locally without GPU:

```text
Analysis window: 30
Process FPS: 2
Replay speed: 8
YOLO confidence: 0.40
YOLO IoU: 0.28
Tracker: Auto profile
```

Why:

- Short enough to process.
- Enough frames for visible tracking.
- Auto profile uses camera-specific tracker choices.

### Better Quality

Use this when you can wait longer:

```text
Analysis window: 60 to 120
Process FPS: 3 to 5
Replay speed: 4 to 8
YOLO confidence: 0.35 to 0.45
YOLO IoU: 0.25 to 0.35
Tracker: Auto profile
```

Why:

- More frames improve event continuity.
- Longer windows provide better funnel/heatmap signals.
- Slight confidence adjustments can recover missed people.

### Faster But Weaker

Use only for quick UI checks:

```text
Analysis window: 10 to 20
Process FPS: 1
Replay speed: 12
```

Expected weakness:

- fewer boxes
- fewer events
- less stable IDs
- weaker heatmap/funnel

---

## 14. How To Improve Output Quality

### If Boxes Are Missing People

Try:

- lower YOLO confidence from `0.40` to `0.35`
- increase image size if available
- increase process FPS
- use better-lit video

Tradeoff:

Lower confidence may detect more people but can also add false positives.

### If Boxes Flicker Or IDs Change Too Often

Try:

- use `Tracker: Auto profile`
- increase process FPS
- use `BoT-SORT` for busy billing/zone views
- use `Centroid` for simple entry-line views

Why:

Different camera angles need different tracker behavior.

### If Mirrors Or Reflections Are Counted

Current options:

- keep the physical-person validation layer enabled
- raise YOLO confidence slightly if weak reflection boxes are still detected
- increase process FPS if real people are being dropped due to unstable tracks
- review suspect red boxes in the overlay to see what was filtered

Useful USP checks:

- green boxes should be countable physical people
- red/suspect boxes should not create events
- the overlay chip should separate valid people from suspect tracks
- if a real person is marked suspect, lower the validation strictness only after
  checking whether the detection is tiny, cut off, or unstable

### If Zone/Queue Metrics Are Empty

Check:

- selected camera role
- selected analysis mode
- layout readiness
- calibration zones for that camera

Explanation:

Person detection can work with only video. Zone/queue analytics need camera
calibration.

### If Conversion Is Zero

Check:

- POS data is uploaded/imported
- POS timestamps overlap the session
- store ID matches
- billing events exist

Explanation:

Confirmed conversion needs POS. Without POS, only billing intent can be
estimated.

### If Reports Show Too Little

Try:

- longer analysis window
- higher process FPS
- correct camera role
- calibrated zones
- POS import

---

## 15. Demo Data Vs Product Data

The project contains demo/sample paths and sample files. They are useful for
repeatable USP testing.

Correct USP behavior:

- Keep demo data behind seed behavior.
- Keep clean testing available with `STORE_AUTO_SEED_EVENTS=0`.
- New uploads should create fresh sessions.
- Reports should use session scope.
- Demo sources should stay useful for repeatable testing.

Do not treat hardcoded sample files as the product foundation.

They are fixtures for testing and demonstration.

---

## 16. Troubleshooting

### Dashboard Loads But Metrics Stay Zero

Possible causes:

- API is not running.
- Dashboard points to wrong API URL.
- Session has not generated events yet.
- Video has no visible people.
- Camera role/layout does not support business events.

Check:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

### Person Boxes Appear Late

This can happen because detection is still processing. Wait for the session
progress to move through detection/replay.

For faster feedback:

- reduce analysis window
- reduce process FPS
- choose a shorter clip

### Session Fails

Check terminal logs.

Common causes:

- video path not accessible
- unsupported video format
- missing dependencies
- YOLO model missing
- layout path invalid

### Port Already In Use

Use another dashboard port:

```powershell
python -m uvicorn dashboard.server:app --reload --host 127.0.0.1 --port 8121
```

Or stop the old process.

### Clean DB Still Shows Old Data

Make sure the API terminal actually has:

```powershell
$env:STORE_DB_PATH="data\dashboard_live.sqlite3"
$env:STORE_AUTO_SEED_EVENTS="0"
$env:STORE_AUTO_LOAD_POS="0"
```

Then restart the API.

---

## 17. What Counts As USP Pass

The USP is passing when:

- A new CCTV run creates a new session.
- Person boxes/IDs appear on video.
- Events are generated for supported camera roles.
- Metrics update only for the current session.
- Reports export successfully.
- Session History can reopen a previous run.
- Tests pass.

One-sentence proof:

> The system turns CCTV footage into visible tracked people, live store metrics,
> and a downloadable session report.
