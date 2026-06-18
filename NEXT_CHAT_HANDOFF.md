# Vivid Store AI - USP Handoff

This document is for any future chat/model that opens this repository.

Do not rely on previous chat memory. Treat repository files as the source of
truth.

---

## Project Identity

Project name:

```text
Vivid Store AI - CCTV Retail Systems
```

Project focus:

```text
Final USP product.
```

USP one-liner:

```text
Turn CCTV footage into visible tracked people, live store metrics, and a
downloadable session-scoped business report.
```

---

## First Instruction For A New Chat

Before making any code changes, inspect the project.

Read these files first:

```text
README.md
NEXT_CHAT_HANDOFF.md
docs\PRODUCT_OVERVIEW.md
docs\RUN_INSTRUCTIONS.md
```

Then inspect:

```text
app\
dashboard\
pipeline\
contracts\
tests\
requirements.txt
```

Do not assume anything that is not present in the files.

---

## What This Repository Should Remain

This repository is the focused USP version of Vivid Store AI.

It should remain centered on:

- CCTV source selection.
- CCTV upload.
- YOLO person detection.
- Camera-specific tracking profiles.
- Video overlay with person boxes and IDs.
- Session-scoped events and metrics.
- Funnel, heatmap, queue, and conversion signal where the camera/layout supports it.
- Data reliability scoring.
- Session History.
- Markdown/CSV/JSON session reports.
- Clear run instructions.
- Reliable tests.

Do not turn this repository into an unrelated planning project. Keep it narrow,
stable, and demo-worthy.

---

## Current USP Capabilities

The project can:

- Run a FastAPI analytics backend.
- Run a live dashboard.
- Use known CCTV clips.
- Accept uploaded CCTV videos.
- Create a new isolated session for each run.
- Run YOLO detection.
- Track detected people.
- Draw bounding boxes and person IDs on video.
- Generate events for supported camera roles:
  - `ENTRY`
  - `EXIT`
  - `REENTRY`
  - `ZONE_ENTER`
  - `ZONE_EXIT`
  - `ZONE_DWELL`
  - `BILLING_QUEUE_JOIN`
  - `BILLING_QUEUE_ABANDON`
- Show live metrics:
  - visitors
  - events
  - queue
  - conversion signal
  - funnel
  - heatmap
  - alerts
  - data reliability
- Export current-session reports:
  - Markdown
  - CSV
  - JSON
- Reopen previous sessions from Session History.

---

## Most Important Files

Runbook and scope:

```text
README.md
docs\PRODUCT_OVERVIEW.md
docs\RUN_INSTRUCTIONS.md
```

Backend:

```text
app\main.py
app\database.py
app\models.py
app\analytics.py
app\operations.py
app\reports.py
app\registry.py
```

Dashboard:

```text
dashboard\server.py
dashboard\static\index.html
```

AI pipeline:

```text
pipeline\detect.py
pipeline\detect_yolo.py
pipeline\tracker.py
pipeline\tracker_profiles.py
pipeline\overlay.py
pipeline\emit.py
pipeline\run_all.py
pipeline\replay.py
```

Contracts:

```text
contracts\store_layout.json
contracts\store_layout_store2.json
contracts\tracker_profiles.json
contracts\camera_time_offsets.json
```

Key tests:

```text
tests\test_phase14_session_reports.py
tests\test_phase15_usp_completion.py
tests\test_live_dashboard.py
tests\test_phase11_tracker_profiles.py
tests\test_phase12_profile_evaluation.py
tests\test_phase13_profile_recommendations.py
```

---

## Run Commands

Terminal 1:

```powershell
cd D:\code\purplletech\projectXD
.\.venv\Scripts\Activate.ps1

$env:STORE_DB_PATH="data\dashboard_live.sqlite3"
$env:STORE_AUTO_SEED_EVENTS="0"
$env:STORE_AUTO_LOAD_POS="0"

python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Terminal 2:

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

---

## Clean Test Database

Use this for clean USP testing:

```powershell
$env:STORE_DB_PATH="data\dashboard_live.sqlite3"
$env:STORE_AUTO_SEED_EVENTS="0"
$env:STORE_AUTO_LOAD_POS="0"
```

Reset only the clean test DB:

```powershell
Remove-Item -LiteralPath data\dashboard_live.sqlite3 -ErrorAction SilentlyContinue
```

Do not randomly delete session artifacts or uploaded videos under `data\`.

---

## Verification

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

Known-good result at USP completion:

```text
Ran 80 tests
OK
```

The test count may increase, but the suite should pass.

---

## Manual USP Pass Criteria

The USP is healthy when:

- a CCTV source can be selected or uploaded
- a new session is created
- video appears
- people are detected
- boxes and IDs appear
- supported events are generated
- metrics update for the current session
- reports export
- Session History can reopen a previous run
- tests pass

---

## Future Work Allowed In This Repository

Allowed:

- bug fixes
- reliability improvements
- clearer run instructions
- better tests
- dashboard clarity
- upload/session/report robustness
- detector/tracker tuning that supports the USP

Avoid:

- broad unrelated expansion
- unrelated platform features
- large architecture rewrites
- changing the product identity away from the CCTV-to-report USP

---

## Suggested Prompt For A New Chat

```text
This is the Vivid Store AI USP repository.
Do not assume previous chat context. Treat repository files as source of truth.

First read:
- README.md
- NEXT_CHAT_HANDOFF.md
- docs/PRODUCT_OVERVIEW.md
- docs/RUN_INSTRUCTIONS.md

Then inspect app/, dashboard/, pipeline/, contracts/, and tests/.

After inspection, summarize:
1. what the USP does,
2. how to run it,
3. whether tests pass,
4. what should remain stable,
5. what the next USP hardening step should be.

Do not make code changes until that summary is complete.
```
