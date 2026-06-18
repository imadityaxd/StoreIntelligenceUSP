Lead Developers - Adittya Sharma, Devyanshuv Agrawal

# Vivid Store AI - CCTV Retail Systems

AI-powered CCTV retail analytics: footage -> tracked people -> live metrics -> session report.

---

## Continuation Handoff

If this project is copied, zipped, moved, or reopened in a new chat, start with:

```text
NEXT_CHAT_HANDOFF.md
```

That file explains the completed USP state, how to run it, and how to keep this
repository focused on the final USP.

---



## Quick Start (6 commands)

```powershell
# 1. Clone / enter project
cd D:\code\purplletech\projectXD

# 2. Create and activate venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the API
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 5. (New terminal) Replay events into the API
python pipeline\replay.py --events data\events_phase8_submission.jsonl --api http://127.0.0.1:8000 --batch-size 100 --print-metrics

# 6. (New terminal) Start the live dashboard
$env:STORE_API_BASE_URL="http://127.0.0.1:8000"
python -m uvicorn dashboard.server:app --reload --host 127.0.0.1 --port 8001
```

Open the dashboard at `http://127.0.0.1:8001`.

Saved CCTV footage is discovered from the project-local media library by
default:

```text
StoreIntelligenceMedia\originals\CCTV Footage
StoreIntelligenceMedia\originals\Store 1
StoreIntelligenceMedia\originals\Store 2
```

To keep footage elsewhere, set `STORE_MEDIA_ROOT` before starting the
dashboard:

```powershell
$env:STORE_MEDIA_ROOT="D:\StoreIntelligenceMedia"
```

---

## Local Run Instructions

Use this path if you want to run the product locally:

```powershell
cd D:\code\purplletech\projectXD
```

For the full runbook, including clean database testing, expected metrics,
manual QA, tuning settings, and output-improvement notes, see:

```text
docs\RUN_INSTRUCTIONS.md
```

### 1. Prepare Python

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If `uvicorn` is not recognized later, run it through Python:

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

### 2. Start The Analytics API

Open terminal 1:

```powershell
cd D:\code\purplletech\projectXD
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check it:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

### 3. Start The Live Dashboard

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

### 4. Run The CCTV Intelligence Demo

In the dashboard:

1. Choose a saved CCTV source from `Live Monitoring`, or upload a new video under `Analyze New Video`.
2. Keep `Show person boxes` enabled.
3. Click `Start Monitoring` for saved sources, or `Start Analysis` for uploaded videos.
4. Watch the video panel, person boxes, live metrics, event feed, funnel, heatmap, and `Live Intelligence` cards update.

For a lighter CPU run, open `ML/Ops Tuning` and use:

```text
Analysis window: 30
Process FPS: 2
Replay speed: 8
```

### 5. Optional: Start With A Clean Demo Database

Use this when you do not want old events mixed into the current run.

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

### 6. Optional: Replay Existing Events Into The API

If you want metrics immediately without running CCTV detection:

```powershell
python pipeline\replay.py `
  --events data\events_phase8_submission.jsonl `
  --api http://127.0.0.1:8000 `
  --store-id STORE_BLR_002 `
  --create-session `
  --batch-size 100 `
  --print-metrics
```

### 7. Verify The Build

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Expected result:

```text
OK
```

---

## Full Workflow

### Environment Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Run CCTV Pipeline

```powershell
python pipeline\run_all.py `
  --video-dir "D:\code\purplletech\CCTV Footage-20260529T160731Z-3-00144614ea\CCTV Footage" `
  --store-id STORE_BLR_002 `
  --yolo-conf 0.40 `
  --yolo-iou 0.28 `
  --yolo-imgsz 960 `
  --tracker-backend auto
```

The USP detector path is YOLO-only and loads local weights from
`yolov8s.pt` by default. MOG2 fallback has been removed. The default tracker
mode is `auto`, which picks a camera-specific profile from
`contracts/tracker_profiles.json`. ByteTrack, BoT-SORT, and the lightweight
centroid fallback remain available through `--tracker-backend`.

### Post-process and Harden Events

```powershell
python scripts\postprocess_events.py `
  --infile data\events_phase5.jsonl `
  --outfile data\events_phase5_tuned2.jsonl `
  --report data\reports\postprocess_report.json

python scripts\harden_submission.py `
  --infile data\events_phase5_tuned2.jsonl `
  --outfile data\events_phase8_submission.jsonl `
  --report-json data\reports\phase8_hardening_report.json `
  --report-md data\reports\phase8_hardening_report.md
```

### Validate Submission Events

```powershell
python scripts\validate_submission_events.py --events data\events_phase8_submission.jsonl
```

### Audit YOLO Detection Quality

```powershell
python scripts\validate_detection_quality.py `
  --frames calibration\reference_frames `
  --report data\reports\validate_yolo_groups.json
```

### Score USP Event Quality

```powershell
python scripts\score_event_quality.py `
  --events data\events_phase8_submission.jsonl `
  --pos data\pos_transactions.json `
  --layout contracts\store_layout.json `
  --report-json data\reports\usp_quality_report.json `
  --report-md data\reports\usp_quality_report.md
```

### Run Tests

```powershell
python -m unittest tests\test_phase2_core.py
python -m unittest tests\test_phase3_business_metrics.py
python -m unittest tests\test_phase4_calibration.py
python -m unittest tests\test_phase5_pipeline_core.py
python -m unittest tests\test_phase6_replay.py
```

### Start the API

```powershell
uvicorn app.main:app --reload
```

### Replay Events (simulated live ingestion)

```powershell
python pipeline\replay.py `
  --events data\events_phase8_submission.jsonl `
  --api http://127.0.0.1:8000 `
  --create-session `
  --batch-size 100 `
  --print-metrics
```

### Start the Live Dashboard

```powershell
uvicorn dashboard.server:app --reload --port 8001
```

The dashboard has two modes:

- Store snapshot mode: polls the analytics API for existing metrics.
- Live video mode: selects a saved CCTV clip, uploaded video, or RTSP URL,
  runs the YOLO detector in a background session, replays emitted events into
  the API, and updates the video panel, KPI cards, funnel, heatmap, alerts, and
  event feed while the session runs.

The dashboard streams KPI snapshots from:

- `GET /stores/{store_id}/overview`
- `GET /stores/{store_id}/metrics`
- `GET /stores/{store_id}/funnel`
- `GET /stores/{store_id}/heatmap`
- `GET /stores/{store_id}/anomalies`
- `GET /stores/{store_id}/events/recent`

Default dashboard URL: `http://127.0.0.1:8001`.

If the analytics API is not running on port `8000`, set the API base URL:

```powershell
$env:STORE_API_BASE_URL="http://127.0.0.1:8000"
uvicorn dashboard.server:app --reload --port 8001
```

For a clean live-video demo database:

```powershell
$env:STORE_DB_PATH="data\dashboard_live.sqlite3"
$env:STORE_AUTO_SEED_EVENTS="0"
$env:STORE_AUTO_LOAD_POS="0"
uvicorn app.main:app --reload --port 8000
```

Then start the dashboard in a second terminal:

```powershell
$env:STORE_API_BASE_URL="http://127.0.0.1:8000"
uvicorn dashboard.server:app --reload --port 8001
```

Open `http://127.0.0.1:8001`, choose a source such as
`STORE_BLR_002 CAM_5 - billing`, and click `Start Monitoring`. For CPU
testing, use a short window such as `30` seconds and `2` process FPS.

---

## Docker

```powershell
docker compose up --build
```

The API will be available at `http://localhost:8000`.
The live dashboard will be available at `http://localhost:8001`.
SQLite data persists in `./data/` via a volume mount. Startup seeding is
idempotent: `data/events_phase8_submission.jsonl` and
`contracts/pos_transactions.csv` are loaded if present, and duplicate event IDs
are skipped on restart.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sessions` | Create a clean analysis/live session |
| `GET` | `/sessions` | List analysis/live sessions |
| `GET` | `/sessions/{session_id}` | Get session metadata and status |
| `POST` | `/sessions/{session_id}/status` | Update processing status for a session |
| `GET` | `/sessions/{session_id}/overview` | Session-scoped dashboard payload |
| `GET` | `/dashboard/sessions/{session_id}/overlays` | Dashboard overlay frames for person boxes, IDs, and zone polygons |
| `POST` | `/events/ingest` | Ingest a list of StoreEvent objects (idempotent) |
| `GET` | `/stores` | Store registry with camera and zone catalog |
| `GET` | `/stores/{store_id}` | Store profile, configured cameras, zones, and data counts |
| `GET` | `/stores/{store_id}/overview` | Product dashboard payload: metrics, funnel, heatmap, anomalies, camera health, quality, recent events |
| `GET` | `/stores/{store_id}/metrics` | Footfall, dwell, conversion metrics |
| `GET` | `/stores/{store_id}/funnel` | Entry -> browse -> billing -> purchase funnel |
| `GET` | `/stores/{store_id}/heatmap` | Zone visit counts |
| `GET` | `/stores/{store_id}/anomalies` | Detected anomalies with reasons |
| `GET` | `/stores/{store_id}/cameras` | Per-camera operational health and event freshness |
| `GET` | `/stores/{store_id}/quality` | Event quality score, confidence distribution, and count breakdowns |
| `GET` | `/stores/{store_id}/events/recent` | Recent event feed for live dashboards |
| `GET` | `/stores/{store_id}/events/search` | Filterable event search by event type, camera, visitor, zone, staff, confidence |
| `GET` | `/stores/{store_id}/visitors/{visitor_id}/timeline` | Visitor-level event timeline and session summary |
| `GET` | `/system/diagnostics` | Runtime diagnostics for DB, layouts, counts, and seed flags |
| `GET` | `/health` | Service health check |

Example store ID from generated data: `STORE_BLR_002`

```powershell
# After docker compose up or uvicorn:
Invoke-WebRequest http://localhost:8000/health | Select-Object -ExpandProperty Content
Invoke-WebRequest http://localhost:8000/stores | Select-Object -ExpandProperty Content
Invoke-WebRequest http://localhost:8000/stores/STORE_BLR_002/overview | Select-Object -ExpandProperty Content
Invoke-WebRequest http://localhost:8000/sessions | Select-Object -ExpandProperty Content
Invoke-WebRequest http://localhost:8000/stores/STORE_BLR_002/metrics | Select-Object -ExpandProperty Content
Invoke-WebRequest http://localhost:8000/stores/STORE_BLR_002/funnel | Select-Object -ExpandProperty Content
Invoke-WebRequest http://localhost:8000/stores/STORE_BLR_002/heatmap | Select-Object -ExpandProperty Content
Invoke-WebRequest http://localhost:8000/stores/STORE_BLR_002/anomalies | Select-Object -ExpandProperty Content
Invoke-WebRequest "http://localhost:8000/stores/STORE_BLR_002/events/search?event_type=ENTRY&limit=10" | Select-Object -ExpandProperty Content
```

---

## Event Output Summary

The pipeline processes 5 cameras:

| Camera | Role | Events Generated |
|--------|------|-----------------|
| CAM_1 | Zone browsing (area A) | ZONE_ENTER / ZONE_EXIT / ZONE_DWELL |
| CAM_2 | Zone browsing (area B) | ZONE_ENTER / ZONE_EXIT / ZONE_DWELL |
| CAM_3 | Store entrance | ENTRY / EXIT |
| CAM_4 | Low-contrast area | 0 events (known limitation) |
| CAM_5 | Billing queue | BILLING_QUEUE_JOIN / BILLING_QUEUE_ABANDON |

Low-confidence events retain their canonical `event_type` with
`metadata.data_confidence_flag = "LOW"` set in the metadata field.
The final hardening step also adds transparent LOW-confidence enrichment for
weak signals that the raw CCTV pipeline cannot fully prove: inferred CAM_3
exits, one re-entry example, staff-like persistent dwell flags, and one
POS-alignment billing anchor for conversion calculation.

---

## Video AI Overlay

Live dashboard sessions now produce a separate overlay artifact beside the
event file:

- `data/live_sessions/<session_id>/events.jsonl`
- `data/live_sessions/<session_id>/overlays.jsonl`

The overlay JSONL is frame-level data used only for visualization. It includes
the current `session_id`, `camera_id`, `frame_index`, `video_time_seconds`,
frame dimensions, and per-person tracks with:

- bounding box in pixels and normalized coordinates
- stable visitor/person ID
- physical-person status (`person` or `suspect`)
- countable flag and physical-person score
- confidence
- zone ID when the footpoint is inside a calibrated zone

The dashboard fetches this through
`/dashboard/sessions/{session_id}/overlays` and draws boxes on the video canvas.
The operator can toggle person boxes and zone outlines from the Live Monitoring
panel.

The tracker also keeps short-lived predicted boxes during brief YOLO misses.
Fresh detections are drawn as solid boxes; predicted boxes are lighter/dashed.
Validation-suspect boxes are marked separately so mirror/reflection-like tracks
can be reviewed without becoming business events. This prevents person IDs from disappearing immediately during motion blur,
sampling gaps, or partial occlusion while still making uncertainty visible.

For a standalone CLI run, add an overlay output path:

```powershell
python pipeline\detect.py `
  --video "D:\code\purplletech\CCTV Footage-20260529T160731Z-3-00144614ea\CCTV Footage\CAM 1.mp4" `
  --camera-id CAM_1 `
  --out data\pipeline\CAM_1.jsonl `
  --overlay-out data\pipeline\CAM_1.overlays.jsonl `
  --tracker-backend auto `
  --max-seconds 10 `
  --process-fps 1
```

For multi-camera runs:

```powershell
python pipeline\run_all.py `
  --video-dir "D:\code\purplletech\Store 1-20260602T101818Z-3-001ec38db8\Store 1" `
  --overlay-dir data\pipeline\overlays `
  --tracker-backend auto
```

To compare tracker choices on a clip without ingesting events:

```powershell
python scripts\compare_tracker_backends.py `
  --video "D:\code\purplletech\Store 1-20260602T101818Z-3-001ec38db8\Store 1\CAM 5 - billing.mp4" `
  --store-id STORE_BLR_002 `
  --camera-id CAM_5 `
  --role billing `
  --max-seconds 20
```

To evaluate Auto profiles across mapped cameras:

```powershell
python scripts\evaluate_tracker_profiles.py `
  --store-id STORE_BLR_002 `
  --max-seconds 5
```

To target one camera, for example the entry camera:

```powershell
python scripts\evaluate_tracker_profiles.py `
  --store-id STORE_BLR_002 `
  --camera-id CAM_3 `
  --backends auto centroid `
  --max-seconds 5
```

To generate safe profile update recommendations from an evaluation report:

```powershell
python scripts\recommend_tracker_profile_updates.py `
  --evaluation-report data\reports\tracker_profile_evaluation.json
```

This only writes recommendation reports. To update `contracts/tracker_profiles.json`,
review the Markdown report first, then rerun with `--apply`.

---

## Session Reports

Each dashboard analysis run now has downloadable current-session reports:

- Markdown: `/dashboard/sessions/<session_id>/report?format=md`
- CSV: `/dashboard/sessions/<session_id>/report?format=csv`
- JSON: `/dashboard/sessions/<session_id>/report?format=json`

The same actions are available in the dashboard `Current Session` card after a
session starts. These reports are scoped to the active CCTV run, so they do not
mix old demo/store history with a new upload or monitoring session.

To verify the report layer:

```powershell
python -m py_compile app\reports.py dashboard\server.py
python -m unittest discover -s tests -p "test_phase14_session_reports.py"
```

---

## USP Complete Flow

The core USP is now implemented end to end:

1. Select a saved CCTV source or upload a CCTV video.
2. Start an isolated dashboard session.
3. YOLO detects people, physical-person validation suppresses weak/reflection-like tracks, and the tracker assigns person IDs.
4. The dashboard draws person boxes/IDs on the video.
5. Session-scoped metrics update for valid people, queue, funnel, heatmap, and
   conversion signal.
6. The completed session can be reopened from `Session History`.
7. The session can be exported as Markdown, CSV, or JSON.

The dashboard defaults to the active/current session, so a new CCTV run does not
silently merge with old store history.

USP verification:

```powershell
python -m py_compile dashboard\server.py app\reports.py
python -m unittest discover -s tests -p "test_phase15_usp_completion.py"
python -m unittest discover -s tests -p "test_*.py"
```

See `docs\RUN_INSTRUCTIONS.md` for the manual smoke test.
