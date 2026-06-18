# main.py
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.analytics import compute_anomalies, compute_funnel, compute_heatmap, compute_metrics, fmt_ts, load_layout, match_pos_to_visitors, parse_ts
from app.database import StoreDatabase
from app.models import (
    PosImportPayload,
    SessionStatus,
    StoreEvent,
    StoreSessionCreate,
    StoreSessionUpdate,
    VisitorStaffCorrectionCreate,
)
from app.operations import (
    build_visitor_timeline,
    compute_camera_health,
    compute_event_quality,
    compute_store_overview,
    filter_events,
)
from app.registry import build_store_profile, load_store_layouts, unique_paths
from scripts.import_pos import parse_mapped_csv, parse_purplle_csv

try:
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover - used only when dependencies are missing.
    raise RuntimeError(
        "FastAPI is not installed. Run `python -m pip install -r requirements.txt`."
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("STORE_DB_PATH", PROJECT_ROOT / "data" / "store_intelligence.sqlite3"))
LAYOUT_PATH = Path(os.getenv("STORE_LAYOUT_PATH", PROJECT_ROOT / "contracts" / "store_layout.json"))
SEED_EVENTS_PATH = Path(os.getenv("STORE_SEED_EVENTS_PATH", PROJECT_ROOT / "data" / "events_phase8_submission.jsonl"))
POS_CSV_PATH = Path(os.getenv("STORE_POS_CSV_PATH", PROJECT_ROOT / "contracts" / "pos_transactions.csv"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("store-intelligence")

app = FastAPI(title="Store Intelligence API", version="0.4.0")
database = StoreDatabase(DB_PATH)


def env_enabled(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def configured_layout_paths() -> list[Path]:
    paths = [LAYOUT_PATH, PROJECT_ROOT / "contracts" / "store_layout_store2.json"]
    extra_paths = os.getenv("STORE_EXTRA_LAYOUT_PATHS", "").strip()
    if extra_paths:
        paths.extend(Path(value) for value in extra_paths.split(os.pathsep) if value.strip())
    return unique_paths(paths)


def layout_registry() -> dict[str, dict[str, Any]]:
    return load_store_layouts(configured_layout_paths())


def resolve_layout_for_store(store_id: str) -> tuple[dict[str, Any], str | None, str]:
    registry = layout_registry()
    if store_id in registry:
        record = registry[store_id]
        return record["layout"], record["layout_path"], "configured"

    if LAYOUT_PATH.exists():
        return load_layout(LAYOUT_PATH), str(LAYOUT_PATH), "fallback_default"

    raise HTTPException(
        status_code=500,
        detail={"error": "layout_not_configured", "store_id": store_id},
    )


def new_session_id(prefix: str = "SESSION") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{timestamp}_{uuid.uuid4().hex[:8].upper()}"


def session_payload(session: StoreSessionCreate) -> dict[str, Any]:
    payload = session.model_dump(mode="json")
    payload["session_id"] = payload["session_id"] or new_session_id()
    payload["status"] = str(payload["status"])
    return payload


def summarize_pos_transactions(transactions: list[dict]) -> dict[str, Any]:
    timestamps = [row["timestamp"] for row in transactions]
    total_sales = round(sum(float(row.get("basket_value_inr", 0)) for row in transactions), 2)
    return {
        "transaction_count": len(transactions),
        "store_ids": sorted({row["store_id"] for row in transactions}),
        "window": {
            "start": min(timestamps) if timestamps else None,
            "end": max(timestamps) if timestamps else None,
        },
        "total_sales_inr": total_sales,
        "sample": transactions[:10],
    }


def apply_staff_corrections(events: list[dict], corrections: list[dict]) -> list[dict]:
    if not corrections:
        return events
    latest: dict[tuple[str | None, str], dict] = {}
    for correction in corrections:
        latest[(correction.get("session_id"), correction["visitor_id"])] = correction

    corrected_events: list[dict] = []
    for event in events:
        session_id = event.get("session_id")
        correction = latest.get((session_id, event["visitor_id"])) or latest.get((None, event["visitor_id"]))
        if not correction:
            corrected_events.append(event)
            continue
        metadata = {
            **(event.get("metadata") or {}),
            "staff_correction_applied": True,
            "staff_correction_id": correction["correction_id"],
            "staff_correction_reason": correction.get("reason"),
        }
        corrected_events.append({**event, "is_staff": bool(correction["is_staff"]), "metadata": metadata})
    return corrected_events


def fetch_corrected_events(
    store_id: str,
    start: str | None = None,
    end: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    events = database.fetch_events(store_id, start, end, session_id=session_id)
    corrections = database.fetch_visitor_corrections(store_id, session_id=session_id)
    return apply_staff_corrections(events, corrections)


def scoped_events_and_pos(
    store_id: str,
    start: str | None = None,
    end: str | None = None,
    session_id: str | None = None,
) -> tuple[list[dict], list[dict]]:
    events = fetch_corrected_events(store_id, start, end, session_id=session_id)
    pos_start = start
    pos_end = end
    if session_id and events and not start and not end:
        timestamps = [event["timestamp"] for event in events]
        pos_start = min(timestamps)
        pos_end = max(timestamps)
    pos = database.fetch_pos_transactions(store_id, pos_start, pos_end)
    return events, pos


def seed_demo_data() -> None:
    if env_enabled("STORE_AUTO_LOAD_POS") and POS_CSV_PATH.exists():
        try:
            pos_rows = parse_purplle_csv(POS_CSV_PATH)
            store_ids = sorted({row["store_id"] for row in pos_rows})
            existing = sum(len(database.fetch_pos_transactions(store_id)) for store_id in store_ids)
            if existing == 0:
                imported = database.upsert_pos_transactions(pos_rows)
                logger.info("loaded_pos_transactions", extra={"event_count": imported})
            else:
                logger.info("pos_transactions_already_loaded", extra={"event_count": existing})
        except Exception as exc:  # pragma: no cover - defensive startup path
            logger.warning("pos_seed_failed", extra={"detail": str(exc)})

    if env_enabled("STORE_AUTO_SEED_EVENTS") and SEED_EVENTS_PATH.exists():
        try:
            events = [
                StoreEvent.model_validate(json.loads(line))
                for line in SEED_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            result = database.insert_events(events)
            logger.info(
                "seeded_submission_events",
                extra={
                    "event_count": result.accepted,
                    "inserted": result.inserted,
                    "duplicates": result.duplicates,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive startup path
            logger.warning("event_seed_failed", extra={"detail": str(exc)})


@app.on_event("startup")
def startup() -> None:
    database.initialize()
    seed_demo_data()



@app.middleware("http")
async def request_logging(request: Request, call_next):
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    started = time.perf_counter()
    status_code = 500
    event_count = None

    # Extract store_id from path if present (e.g. /stores/STORE_BLR_002/metrics)
    path_parts = request.url.path.strip("/").split("/")
    store_id: str | None = None
    if len(path_parts) >= 2 and path_parts[0] == "stores":
        store_id = path_parts[1]

    try:
        if request.url.path == "/events/ingest":
            body = await request.body()
            request.state.cached_body = body
        response = await call_next(request)
        status_code = response.status_code
        event_count = getattr(request.state, "event_count", None)
        response.headers["x-trace-id"] = trace_id
        return response
    finally:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        log_payload = {
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "method": request.method,
            "latency_ms": latency_ms,
            "event_count": event_count,
            "status_code": status_code,
        }
        # Choose log level based on status code
        if status_code >= 500:
            logger.error("request", extra=log_payload)
        elif status_code >= 400:
            logger.warning("request", extra=log_payload)
        else:
            logger.info("request", extra=log_payload)


async def read_json_body(request: Request) -> Any:
    if hasattr(request.state, "cached_body"):
        body = request.state.cached_body
        if not body:
            return None
        return await request.json()
    return await request.json()


def event_payloads_from_body(body: Any) -> list[Any]:
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("events"), list):
        return body["events"]
    raise HTTPException(
        status_code=400,
        detail={"error": "request body must be a JSON array or {'events': [...]}"},
    )


@app.post("/events/ingest")
async def ingest_events(request: Request):
    body = await read_json_body(request)
    payloads = event_payloads_from_body(body)
    batch_session_id = body.get("session_id") if isinstance(body, dict) else None
    request.state.event_count = len(payloads)

    if len(payloads) > 500:
        raise HTTPException(status_code=413, detail={"error": "batch size must be <= 500"})

    valid_events: list[StoreEvent] = []
    validation_errors: list[dict[str, Any]] = []

    for index, payload in enumerate(payloads):
        try:
            if batch_session_id and isinstance(payload, dict) and not payload.get("session_id"):
                payload = {**payload, "session_id": batch_session_id}
            valid_events.append(StoreEvent.model_validate(payload))
        except ValidationError as exc:
            validation_errors.append({"index": index, "errors": exc.errors()})

    try:
        result = database.insert_events(valid_events)
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"error": "database_unavailable", "detail": str(exc)},
        )

    return {
        "accepted": result.accepted,
        "inserted": result.inserted,
        "duplicates": result.duplicates,
        "duplicate_event_ids": result.duplicate_event_ids,
        "validation_errors": validation_errors,
    }


@app.post("/sessions")
def create_session(session: StoreSessionCreate):
    payload = session_payload(session)
    created = database.create_session(payload)
    return {"session": created}


@app.get("/sessions")
def list_sessions(store_id: str | None = None, status: str | None = None, limit: int = 50):
    sessions = database.list_sessions(store_id=store_id, status=status, limit=limit)
    return {"count": len(sessions), "sessions": sessions}


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = database.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail={"error": "session_not_found", "session_id": session_id})
    return {"session": session}


@app.post("/sessions/{session_id}/status")
def update_session_status(session_id: str, update: StoreSessionUpdate):
    if not database.get_session(session_id):
        raise HTTPException(status_code=404, detail={"error": "session_not_found", "session_id": session_id})
    payload = update.model_dump(mode="json", exclude_none=True)
    if "status" in payload:
        payload["status"] = str(payload["status"])
    updated = database.update_session(session_id, payload)
    return {"session": updated}


@app.get("/sessions/{session_id}/overview")
def get_session_overview(session_id: str):
    session = database.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail={"error": "session_not_found", "session_id": session_id})
    store_id = session["store_id"]
    events, pos = scoped_events_and_pos(store_id, session_id=session_id)
    layout, layout_path, layout_status = resolve_layout_for_store(store_id)
    overview = compute_store_overview(
        events,
        pos,
        layout,
        recent_events=fetch_corrected_events(store_id, session_id=session_id)[-20:],
    )
    return {
        "store_id": store_id,
        "session_id": session_id,
        "session": session,
        "generated_at": fmt_ts(datetime.now(timezone.utc)),
        "layout_status": layout_status,
        "store_profile": build_store_profile(layout, layout_path),
        **overview,
    }


def clean_mapping_value(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def has_pos_mapping(mapping: dict[str, str | None]) -> bool:
    return any(value for key, value in mapping.items() if key != "timezone_offset")


async def parse_uploaded_pos_file(
    file: UploadFile,
    store_id: str,
    mapping: dict[str, str | None] | None = None,
) -> list[dict]:
    if not file.filename:
        raise HTTPException(status_code=400, detail={"error": "filename_required"})
    suffix = Path(file.filename).suffix.lower()
    if suffix != ".csv":
        raise HTTPException(status_code=400, detail={"error": "unsupported_pos_file", "detail": "POS upload must be a CSV file"})
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as handle:
        temp_path = Path(handle.name)
        while chunk := await file.read(1024 * 1024):
            handle.write(chunk)
    try:
        clean_mapping = mapping or {}
        if has_pos_mapping(clean_mapping):
            transactions = parse_mapped_csv(
                temp_path,
                transaction_id_column=clean_mapping["transaction_id_column"] or "transaction_id",
                amount_column=clean_mapping["amount_column"] or "amount",
                store_id_column=clean_mapping.get("store_id_column"),
                timestamp_column=clean_mapping.get("timestamp_column"),
                date_column=clean_mapping.get("date_column"),
                time_column=clean_mapping.get("time_column"),
                timezone_offset=clean_mapping.get("timezone_offset") or "+05:30",
                default_store_id=store_id,
            )
        else:
            transactions = parse_purplle_csv(temp_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "pos_parse_failed", "detail": str(exc)}) from exc
    finally:
        temp_path.unlink(missing_ok=True)
        await file.close()
    for transaction in transactions:
        transaction["store_id"] = store_id
    return transactions


@app.post("/stores/{store_id}/pos/preview")
async def preview_pos_upload(
    store_id: str,
    file: UploadFile = File(...),
    transaction_id_column: str | None = Form(default=None),
    amount_column: str | None = Form(default=None),
    timestamp_column: str | None = Form(default=None),
    date_column: str | None = Form(default=None),
    time_column: str | None = Form(default=None),
    store_id_column: str | None = Form(default=None),
    timezone_offset: str | None = Form(default="+05:30"),
):
    transactions = await parse_uploaded_pos_file(
        file,
        store_id,
        {
            "transaction_id_column": clean_mapping_value(transaction_id_column),
            "amount_column": clean_mapping_value(amount_column),
            "timestamp_column": clean_mapping_value(timestamp_column),
            "date_column": clean_mapping_value(date_column),
            "time_column": clean_mapping_value(time_column),
            "store_id_column": clean_mapping_value(store_id_column),
            "timezone_offset": clean_mapping_value(timezone_offset) or "+05:30",
        },
    )
    return {"store_id": store_id, "imported": False, **summarize_pos_transactions(transactions)}


@app.post("/stores/{store_id}/pos/import")
async def import_pos_upload(
    store_id: str,
    file: UploadFile = File(...),
    transaction_id_column: str | None = Form(default=None),
    amount_column: str | None = Form(default=None),
    timestamp_column: str | None = Form(default=None),
    date_column: str | None = Form(default=None),
    time_column: str | None = Form(default=None),
    store_id_column: str | None = Form(default=None),
    timezone_offset: str | None = Form(default="+05:30"),
):
    transactions = await parse_uploaded_pos_file(
        file,
        store_id,
        {
            "transaction_id_column": clean_mapping_value(transaction_id_column),
            "amount_column": clean_mapping_value(amount_column),
            "timestamp_column": clean_mapping_value(timestamp_column),
            "date_column": clean_mapping_value(date_column),
            "time_column": clean_mapping_value(time_column),
            "store_id_column": clean_mapping_value(store_id_column),
            "timezone_offset": clean_mapping_value(timezone_offset) or "+05:30",
        },
    )
    imported = database.upsert_pos_transactions(transactions)
    return {"store_id": store_id, "imported": True, "upserted": imported, **summarize_pos_transactions(transactions)}


@app.post("/stores/{store_id}/pos/import-json")
def import_pos_json(store_id: str, payload: PosImportPayload):
    transactions = []
    for transaction in payload.transactions:
        row = transaction.model_dump(mode="json")
        row["store_id"] = store_id
        transactions.append(row)
    imported = database.upsert_pos_transactions(transactions)
    return {"store_id": store_id, "imported": True, "upserted": imported, **summarize_pos_transactions(transactions)}


@app.get("/stores/{store_id}/pos/matches")
def get_pos_matches(store_id: str, start: str | None = None, end: str | None = None, session_id: str | None = None):
    events, pos = scoped_events_and_pos(store_id, start, end, session_id)
    match = match_pos_to_visitors(events, pos)
    metrics = compute_metrics(events, pos)
    return {
        "store_id": store_id,
        "session_id": session_id,
        "conversion": metrics["conversion"],
        "pos": metrics["pos"],
        "matches": match["matches"],
        "unmatched_pos_transaction_ids": match["unmatched_pos_transaction_ids"],
        "unmatched_billing_visitor_ids": match["unmatched_billing_visitor_ids"],
    }


@app.get("/stores/{store_id}/staff-corrections")
def get_staff_corrections(store_id: str, session_id: str | None = None):
    corrections = database.fetch_visitor_corrections(store_id, session_id=session_id)
    return {"store_id": store_id, "session_id": session_id, "count": len(corrections), "corrections": corrections}


@app.post("/stores/{store_id}/visitors/{visitor_id}/staff-correction")
def create_staff_correction(store_id: str, visitor_id: str, correction: VisitorStaffCorrectionCreate):
    row = database.upsert_visitor_correction(
        {
            "store_id": store_id,
            "visitor_id": visitor_id,
            "session_id": correction.session_id,
            "is_staff": correction.is_staff,
            "reason": correction.reason,
            "metadata": correction.metadata,
        }
    )
    events = fetch_corrected_events(store_id, session_id=correction.session_id)
    return {
        "store_id": store_id,
        "visitor_id": visitor_id,
        "correction": row,
        "affected_event_count": sum(1 for event in events if event["visitor_id"] == visitor_id),
    }


@app.get("/stores")
def list_stores():
    registry = layout_registry()
    event_counts = database.event_count_by_store()
    pos_counts = database.pos_count_by_store()
    store_ids = sorted(set(registry) | set(event_counts) | set(pos_counts))

    stores = []
    for store_id in store_ids:
        if store_id in registry:
            record = registry[store_id]
            profile = build_store_profile(
                record["layout"],
                record["layout_path"],
                event_count=event_counts.get(store_id, 0),
                pos_transaction_count=pos_counts.get(store_id, 0),
            )
            profile["layout_status"] = "configured"
        else:
            profile = {
                "store_id": store_id,
                "store_name": store_id,
                "timezone": None,
                "open_hours": None,
                "layout_path": None,
                "camera_count": 0,
                "zone_count": 0,
                "cameras": [],
                "zones": [],
                "event_count": event_counts.get(store_id, 0),
                "pos_transaction_count": pos_counts.get(store_id, 0),
                "layout_status": "missing",
            }
        stores.append(profile)

    return {"count": len(stores), "stores": stores}


@app.get("/stores/{store_id}")
def get_store_profile(store_id: str):
    layout, layout_path, layout_status = resolve_layout_for_store(store_id)
    profile = build_store_profile(
        layout,
        layout_path,
        event_count=database.count_events(store_id),
        pos_transaction_count=database.count_pos_transactions(store_id),
    )
    profile["requested_store_id"] = store_id
    profile["layout_status"] = layout_status
    profile["session_count"] = database.count_sessions(store_id)
    return profile


@app.get("/stores/{store_id}/overview")
def get_store_overview(
    store_id: str,
    start: str | None = None,
    end: str | None = None,
    session_id: str | None = None,
):
    events, pos = scoped_events_and_pos(store_id, start, end, session_id)
    layout, layout_path, layout_status = resolve_layout_for_store(store_id)
    overview = compute_store_overview(
        events,
        pos,
        layout,
        recent_events=fetch_corrected_events(store_id, start, end, session_id)[-20:],
    )
    return {
        "store_id": store_id,
        "session_id": session_id,
        "session": database.get_session(session_id) if session_id else None,
        "generated_at": fmt_ts(datetime.now(timezone.utc)),
        "layout_status": layout_status,
        "store_profile": build_store_profile(layout, layout_path),
        **overview,
    }


@app.get("/stores/{store_id}/metrics")
def get_metrics(store_id: str, start: str | None = None, end: str | None = None, session_id: str | None = None):
    events, pos = scoped_events_and_pos(store_id, start, end, session_id)
    layout, _, _ = resolve_layout_for_store(store_id)
    metrics = compute_metrics(events, pos, layout)
    return {"store_id": store_id, "session_id": session_id, **metrics}


@app.get("/stores/{store_id}/funnel")
def get_funnel(store_id: str, start: str | None = None, end: str | None = None, session_id: str | None = None):
    events, pos = scoped_events_and_pos(store_id, start, end, session_id)
    return {"store_id": store_id, "session_id": session_id, **compute_funnel(events, pos)}


@app.get("/stores/{store_id}/heatmap")
def get_heatmap(store_id: str, start: str | None = None, end: str | None = None, session_id: str | None = None):
    events = fetch_corrected_events(store_id, start, end, session_id=session_id)
    layout, _, _ = resolve_layout_for_store(store_id)
    return {"store_id": store_id, "session_id": session_id, **compute_heatmap(events, layout)}


@app.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str, start: str | None = None, end: str | None = None, session_id: str | None = None):
    events, pos = scoped_events_and_pos(store_id, start, end, session_id)
    layout, _, _ = resolve_layout_for_store(store_id)
    return {"store_id": store_id, "session_id": session_id, "anomalies": compute_anomalies(events, pos, layout)}


@app.get("/stores/{store_id}/cameras")
def get_camera_health(
    store_id: str,
    start: str | None = None,
    end: str | None = None,
    session_id: str | None = None,
    stale_after_seconds: int = 600,
):
    events = fetch_corrected_events(store_id, start, end, session_id=session_id)
    layout, _, layout_status = resolve_layout_for_store(store_id)
    return {
        "store_id": store_id,
        "session_id": session_id,
        "layout_status": layout_status,
        "stale_after_seconds": stale_after_seconds,
        "cameras": compute_camera_health(events, layout, stale_after_seconds=stale_after_seconds),
    }


@app.get("/stores/{store_id}/quality")
def get_event_quality(store_id: str, start: str | None = None, end: str | None = None, session_id: str | None = None):
    events = fetch_corrected_events(store_id, start, end, session_id=session_id)
    return {"store_id": store_id, "session_id": session_id, **compute_event_quality(events)}


@app.get("/stores/{store_id}/events/recent")
def get_recent_events(store_id: str, limit: int = 50, session_id: str | None = None):
    return {
        "store_id": store_id,
        "session_id": session_id,
        "events": fetch_corrected_events(store_id, session_id=session_id)[-max(1, min(int(limit), 200)):],
    }


@app.get("/stores/{store_id}/events/search")
def search_events(
    store_id: str,
    start: str | None = None,
    end: str | None = None,
    event_type: str | None = None,
    camera_id: str | None = None,
    visitor_id: str | None = None,
    zone_id: str | None = None,
    is_staff: bool | None = None,
    low_confidence: bool | None = None,
    session_id: str | None = None,
    limit: int = 100,
    sort: str = "desc",
):
    events = fetch_corrected_events(store_id, start, end, session_id=session_id)
    rows = filter_events(
        events,
        event_type=event_type,
        camera_id=camera_id,
        visitor_id=visitor_id,
        zone_id=zone_id,
        is_staff=is_staff,
        low_confidence=low_confidence,
        limit=limit,
        sort=sort,
    )
    return {"store_id": store_id, "session_id": session_id, "count": len(rows), "events": rows}


@app.get("/stores/{store_id}/visitors/{visitor_id}/timeline")
def get_visitor_timeline(
    store_id: str,
    visitor_id: str,
    start: str | None = None,
    end: str | None = None,
    session_id: str | None = None,
):
    events = fetch_corrected_events(store_id, start, end, session_id=session_id)
    return {"store_id": store_id, "session_id": session_id, **build_visitor_timeline(events, visitor_id)}


@app.get("/system/diagnostics")
def get_system_diagnostics():
    registry = layout_registry()
    event_counts = database.event_count_by_store()
    pos_counts = database.pos_count_by_store()
    return {
        "service": {
            "name": "Store Intelligence API",
            "version": app.version,
            "generated_at": fmt_ts(datetime.now(timezone.utc)),
        },
        "database": {
            "path": str(DB_PATH),
            "exists": DB_PATH.exists() if str(DB_PATH) != ":memory:" else True,
            "event_count": database.count_events(),
            "session_count": database.count_sessions(),
            "pos_transaction_count": database.count_pos_transactions(),
            "event_count_by_store": event_counts,
            "pos_count_by_store": pos_counts,
        },
        "layouts": [
            {
                "store_id": store_id,
                "store_name": record["layout"].get("store_name"),
                "layout_path": record["layout_path"],
                "camera_count": len(record["layout"].get("cameras", [])),
                "zone_count": len(record["layout"].get("zones", [])),
            }
            for store_id, record in sorted(registry.items())
        ],
        "runtime_flags": {
            "auto_seed_events": env_enabled("STORE_AUTO_SEED_EVENTS"),
            "auto_load_pos": env_enabled("STORE_AUTO_LOAD_POS"),
            "seed_events_path": str(SEED_EVENTS_PATH),
            "pos_csv_path": str(POS_CSV_PATH),
            "layout_paths": [str(path) for path in configured_layout_paths()],
        },
    }


@app.get("/health")
def health():
    try:
        database.initialize()
        stores = database.last_event_by_store()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "ERROR", "database": "unavailable", "detail": str(exc)},
        )

    now = time.time()
    enriched = []
    for store in stores:
        event_time = store["last_event_timestamp"]
        parsed = parse_ts(event_time).timestamp()
        lag_seconds = max(0, round(now - parsed, 2))
        enriched.append(
            {
                **store,
                "lag_seconds": lag_seconds,
                "feed_status": "STALE_FEED" if lag_seconds > 600 else "OK",
            }
        )

    return {"status": "OK", "database": "ok", "stores": enriched}
