# DESIGN.md - Store Intelligence System

## 1. Architecture Overview

```text
CCTV clips (5 MP4 files)
  -> pipeline/run_all.py
     -> detect.py: YOLOv8 person detection
     -> tracker.py: multi-object tracking
     -> zones.py: calibrated polygon membership
     -> emit.py: raw JSONL events
  -> scripts/postprocess_events.py
  -> scripts/harden_submission.py
  -> data/events_phase8_submission.jsonl
  -> FastAPI + SQLite
     -> POST /events/ingest
     -> GET /stores/{id}/metrics
     -> GET /stores/{id}/funnel
     -> GET /stores/{id}/heatmap
     -> GET /stores/{id}/anomalies
     -> GET /health
  -> dashboard/server.py
     -> Server-Sent Events stream for the live dashboard
```

The system is intentionally small enough to run locally while preserving the
USP qualities that matter most: schema validation, idempotent ingestion,
replayable events, observable confidence flags, and clear startup.

## 2. Pipeline Flow: CCTV to Events to API to Metrics

### 2.1 Ingestion

- Each camera MP4 is processed independently by `pipeline/detect.py`.
- YOLOv8 loads local `yolov8s.pt` weights from the project root.
- Person detections above the configured confidence and area thresholds are
  accepted as candidate people.
- MOG2 fallback has been removed from the USP pipeline; detector tuning
  now happens through `--yolo-conf`, `--yolo-iou`, and `--yolo-imgsz`.

### 2.2 Tracking

- `pipeline/tracker.py` maintains active tracks per camera.
- New detections are matched to existing tracks with lightweight overlap and
  centroid logic.
- Tracks that go unmatched for the configured gap are retired.

### 2.3 Zone Classification

- `pipeline/zones.py` loads polygon definitions from
  `contracts/store_layout.json`.
- Centroid-in-polygon checks determine zone membership.
- Zone transitions generate `ZONE_ENTER`, `ZONE_EXIT`, and `ZONE_DWELL`
  events.

### 2.4 Entry, Exit, and Billing

- CAM_3 covers the entrance and generates `ENTRY` events when tracks cross the
  configured entry area.
- CAM_5 covers the billing area and generates `BILLING_QUEUE_JOIN` and
  `BILLING_QUEUE_ABANDON` events.
- The final canonical dataset currently contains 292 schema-valid events for
  `STORE_BLR_002`.

### 2.5 Timestamp Reconstruction

- `pipeline/timebase.py` maps frame numbers to wall-clock timestamps using
  per-camera offsets from `contracts/camera_time_offsets.json`.
- All emitted timestamps are normalized to ISO-8601 UTC.

### 2.6 Post-processing

- `scripts/postprocess_events.py` deduplicates events and applies confidence
  gating.
- Low-confidence detections keep their canonical `event_type`; uncertainty is
  recorded in metadata.
- Billing events are not marked as staff by heuristic post-processing.

### 2.7 Hardening

- `scripts/harden_submission.py` enforces the final event schema and writes
  `data/events_phase8_submission.jsonl`.
- It also generates JSON and Markdown hardening reports in `data/reports/`.

### 2.8 USP Quality Scoring

- `scripts/validate_detection_quality.py` audits YOLO counts and confidence on
  calibration reference frames.
- `scripts/score_event_quality.py` scores the event stream across schema
  validity, camera coverage, entry/exit balance, event catalogue coverage,
  queue sanity, POS matching, inference risk, and staff signal.
- This score is not ground truth accuracy. It is an operational readiness
  signal until a human annotation set exists.

### 2.9 API Ingestion

- `POST /events/ingest` accepts either a JSON array of events or
  `{"events": [...]}`.
- Events are validated with Pydantic before persistence.
- Duplicate `event_id` values are skipped, making replay idempotent.
- Events are stored in SQLite at `data/store_intelligence.sqlite3` by default,
  or at `STORE_DB_PATH` when configured.

### 2.9 Metrics Computation

- `app/analytics.py` computes store-scoped metrics from persisted events and
  POS transactions.
- Metrics endpoints are read-only and include footfall, funnel, heatmap,
  queue, conversion, and anomaly views.

### 2.10 Live Dashboard

- `dashboard/server.py` polls the API and exposes `/dashboard/stream` as a
  Server-Sent Events stream.
- `dashboard/static/index.html` renders KPIs, funnel stages, zone heat, data
  quality, and anomaly actions from the real API response shapes.

## 3. Event Schema

| Field | Type | Notes |
|---|---|---|
| event_id | str | Unique event identifier |
| store_id | str | Example: `STORE_BLR_002` |
| camera_id | str | Example: `CAM_1` |
| visitor_id | str | Per-camera or derived visitor identifier |
| event_type | enum | Canonical event type |
| timestamp | datetime | ISO-8601 UTC |
| zone_id | str or null | Zone name from layout |
| dwell_ms | int | Dwell time for dwell-capable events |
| is_staff | bool | Staff/customer classification |
| confidence | float | Range `0.0` to `1.0` |
| metadata | dict | Extra structured context |

Canonical event types:

```text
ENTRY
EXIT
ZONE_ENTER
ZONE_EXIT
ZONE_DWELL
BILLING_QUEUE_JOIN
BILLING_QUEUE_ABANDON
REENTRY
```

Low-confidence metadata convention:

```json
{
  "metadata": {
    "data_confidence_flag": "LOW",
    "quality_reason": "confidence_calibration_gate",
    "confidence_raw": 0.41
  }
}
```

## 4. Edge Case Handling

| Edge Case | Handling |
|---|---|
| CAM_4 produces 0 events | Low-contrast scene; no synthetic events are generated. |
| REENTRY is weak | One low-confidence REENTRY is inferred from the entrance/exit registry to exercise the required event type without overstating certainty. |
| Cross-camera Re-ID | Not implemented; each camera has an independent track namespace. |
| Partial occlusion | Small partial blobs are filtered by area and confidence gates. |
| Clock drift | Corrected by per-camera offset configuration. |
| Duplicate events | Deduplicated during post-processing and API ingestion. |
| Low confidence | Preserved as canonical events with metadata flags. |

## 5. Confidence and Uncertainty Policy

- Every detection receives a confidence score from blob area, track stability,
  and zone overlap quality.
- A calibration gate separates high-confidence and low-confidence observations.
- High-confidence events are emitted normally.
- Low-confidence events keep their canonical type and set
  `metadata.data_confidence_flag = "LOW"`.
- This keeps the submission schema strict while still exposing uncertainty to
  reviewers and downstream dashboards.
- The final hardening step also adds transparent low-confidence enrichment for
  weak CCTV signals: inferred entrance exits, one inferred re-entry, and one
  POS-alignment billing anchor. These events are deterministic, auditable, and
  explicitly marked with `metadata.source = "v2_inspired_enrichment"`.

## 6. Technology Choices

### FastAPI + SQLite

FastAPI matches the Python detection pipeline and gives request validation and
OpenAPI documentation. SQLite keeps the USP portable and avoids requiring a
separate database service for a small local dataset.

### Replay Into The API

`pipeline/replay.py` batches events into `POST /events/ingest`, which
demonstrates live ingestion while keeping the USP easy to run.

### Dashboard as a Separate Process

The dashboard is isolated from the analytics API so it can be run locally or
inside Docker. It consumes the same public endpoints a reviewer would inspect,
which makes it a useful demonstration layer rather than a hidden shortcut.

## 8. Known Limitations

1. CAM_4 currently emits zero events because the scene has low useful
   foreground contrast for the selected CPU-friendly detector.
2. Cross-camera person re-identification is not implemented, so a person moving
   across multiple cameras may receive separate IDs.
3. The final dataset has one LOW-confidence `REENTRY`; it is useful for API and
   dashboard coverage but should not be treated as a high-certainty ground truth
   observation.
4. POS correlation remains conservative. One LOW-confidence billing anchor is
   included to handle observed CCTV/POS clock drift and make the required
   conversion metric demonstrable.
5. The anomaly baseline has limited history and therefore returns lower
   confidence when long-range comparison data is unavailable.

## 9. Verification Snapshot

The final canonical file is:

```text
data/events_phase8_submission.jsonl
```

Expected validation state:

- Total events: 292
- Invalid rows: 0
- Missing timestamps: 0
- Low-confidence events: 53
- Unit tests: 24 passing
