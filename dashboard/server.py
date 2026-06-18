from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.reports import build_session_report, render_session_report_csv, render_session_report_markdown
from pipeline.overlay import read_overlay_jsonl
from pipeline.tracker_profiles import apply_auto_profile, profile_for_camera
from scripts.import_pos import parse_mapped_csv, parse_purplle_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DATA_DIR = PROJECT_ROOT / "data"
MEDIA_LIBRARY_ROOT = Path(
    os.getenv("STORE_MEDIA_ROOT", PROJECT_ROOT / "StoreIntelligenceMedia")
).expanduser().resolve()
ORIGINAL_MEDIA_DIR = MEDIA_LIBRARY_ROOT / "originals"
UPLOAD_DIR = MEDIA_LIBRARY_ROOT / "uploads"
UPLOAD_REGISTRY_PATH = UPLOAD_DIR / "sources.json"
LIVE_SESSION_DIR = MEDIA_LIBRARY_ROOT / "sessions"
CALIBRATION_DIR = DATA_DIR / "calibrations"
CALIBRATION_FRAME_DIR = CALIBRATION_DIR / "reference_frames"
CALIBRATION_LAYOUT_DIR = CALIBRATION_DIR / "layouts"
CALIBRATION_REGISTRY_PATH = CALIBRATION_DIR / "registry.json"
DEFAULT_TIME_OFFSETS = PROJECT_ROOT / "contracts" / "camera_time_offsets.json"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "yolov8s.pt"
DEFAULT_STORE_LAYOUT = PROJECT_ROOT / "contracts" / "store_layout.json"
STORE2_LAYOUT = PROJECT_ROOT / "contracts" / "store_layout_store2.json"

API_BASE_URL = os.getenv("STORE_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("DASHBOARD_API_TIMEOUT_SECONDS", "4"))
POLL_SECONDS = float(os.getenv("DASHBOARD_POLL_SECONDS", "1"))
MAX_UPLOAD_MB = int(os.getenv("DASHBOARD_MAX_UPLOAD_MB", "2048"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

MEDIA_ROOTS = [PROJECT_ROOT.resolve(), MEDIA_LIBRARY_ROOT]
LAYOUT_ROOTS = [(PROJECT_ROOT / "contracts").resolve(), CALIBRATION_LAYOUT_DIR.resolve()]
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
ALLOWED_CAMERA_ROLES = {"zone", "entry", "billing", "staff-area", "detection_only", "uploaded", "live"}
ANALYSIS_MODE_LABELS = {
    "full_store": "Full store analysis",
    "detection_only": "Detection only",
    "entry_exit": "Entry/exit analysis",
    "zone": "Zone analysis",
    "billing": "Billing queue analysis",
}
PROGRESS_STEPS = [
    ("upload", "Upload complete"),
    ("queued", "Waiting for worker"),
    ("detection", "Running detection"),
    ("events", "Generating events"),
    ("pos", "Matching POS"),
    ("report", "Building result"),
    ("complete", "Complete"),
]

app = FastAPI(title="Store Intelligence Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_STOPS: dict[str, threading.Event] = {}
SESSION_LOCK = threading.Lock()


class LiveSessionRequest(BaseModel):
    source_id: str | None = None
    video_path: str | None = None
    rtsp_url: str | None = None
    camera_id: str | None = None
    store_id: str | None = None
    camera_role: str | None = None
    session_label: str | None = None
    analysis_mode: str = Field(default="full_store")
    layout_path: str | None = None
    max_seconds: float = Field(default=180.0, ge=3.0, le=900.0)
    process_fps: float = Field(default=3.0, ge=0.25, le=12.0)
    replay_speed: float = Field(default=8.0, ge=0.1, le=120.0)
    min_area: int = Field(default=1300, ge=100, le=100_000)
    yolo_conf: float = Field(default=0.40, ge=0.05, le=0.95)
    yolo_iou: float = Field(default=0.28, ge=0.05, le=0.95)
    yolo_imgsz: int = Field(default=960, ge=320, le=1920)
    tracker_backend: str = Field(default="auto", pattern="^(auto|bytetrack|botsort|centroid)$")


class CalibrationReferenceRequest(BaseModel):
    source_id: str | None = None
    video_path: str | None = None
    store_id: str = Field(default="STORE_BLR_002", min_length=1)
    camera_id: str = Field(default="CAM_1", min_length=1)
    camera_role: str = "zone"
    timestamp_seconds: float = Field(default=10.0, ge=0.0)


class CalibrationZonePayload(BaseModel):
    zone_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: str = Field(default="product_zone", min_length=1)
    sku_zone: str | None = None
    polygon_normalized: list[list[float]] = Field(min_length=3)


class CalibrationSaveRequest(BaseModel):
    store_id: str = Field(default="STORE_BLR_002", min_length=1)
    camera_id: str = Field(default="CAM_1", min_length=1)
    camera_role: str = "zone"
    source_id: str | None = None
    source_label: str | None = None
    reference_frame_path: str | None = None
    base_layout_path: str | None = None
    zones: list[CalibrationZonePayload] = Field(default_factory=list)
    entry_line_normalized: list[list[float]] | None = None
    notes: str | None = None

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_download_name(value: str, suffix: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_")
    return f"{cleaned or 'session_report'}{suffix}"


def fetch_json(path: str) -> dict[str, Any]:
    request = Request(f"{API_BASE_URL}{path}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def post_json(path: str, payload: Any) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{API_BASE_URL}{path}",
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


async def fetch_store_snapshot(store_id: str, session_id: str | None = None) -> dict[str, Any]:
    encoded_store = quote(store_id, safe="")
    query = f"?{urlencode({'session_id': session_id})}" if session_id else ""
    try:
        overview = await asyncio.to_thread(fetch_json, f"/stores/{encoded_store}/overview{query}")
        overview.setdefault("store_id", store_id)
        overview.setdefault("api_base_url", API_BASE_URL)
        overview.setdefault("updated_at", overview.get("generated_at", now_iso()))
        overview.setdefault("metrics", {})
        overview.setdefault("funnel", {})
        overview.setdefault("heatmap", {})
        overview.setdefault("anomalies", [])
        overview.setdefault("camera_health", [])
        overview.setdefault("quality", {})
        overview.setdefault("recent_events", [])
        overview.setdefault("errors", {})
        return overview
    except Exception as overview_error:
        endpoints = {
            "metrics": f"/stores/{encoded_store}/metrics",
            "funnel": f"/stores/{encoded_store}/funnel",
            "heatmap": f"/stores/{encoded_store}/heatmap",
            "anomalies_raw": f"/stores/{encoded_store}/anomalies",
            "recent_raw": f"/stores/{encoded_store}/events/recent?limit=30",
        }
        if session_id:
            endpoints = {
                key: f"{path}{'&' if '?' in path else '?'}{urlencode({'session_id': session_id})}"
                for key, path in endpoints.items()
            }
        results = await asyncio.gather(
            *(asyncio.to_thread(fetch_json, path) for path in endpoints.values()),
            return_exceptions=True,
        )
        payload = dict(zip(endpoints.keys(), results))
        errors = {
            "overview": str(overview_error),
            **{
                key: str(value)
                for key, value in payload.items()
                if isinstance(value, Exception)
            },
        }

        anomalies_response = payload.get("anomalies_raw")
        recent_response = payload.get("recent_raw")
        anomalies = anomalies_response.get("anomalies", []) if isinstance(anomalies_response, dict) else []
        recent_events = recent_response.get("events", []) if isinstance(recent_response, dict) else []

        return {
            "store_id": store_id,
            "api_base_url": API_BASE_URL,
            "updated_at": now_iso(),
            "metrics": payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
            "funnel": payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {},
            "heatmap": payload.get("heatmap") if isinstance(payload.get("heatmap"), dict) else {},
            "anomalies": anomalies,
            "camera_health": [],
            "quality": {},
            "recent_events": recent_events,
            "errors": errors,
        }


def safe_media_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    for root in MEDIA_ROOTS:
        try:
            path.relative_to(root)
            if path.exists() and path.is_file():
                return path
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="media path is outside allowed roots")


def default_layout_for_store(store_id: str | None) -> Path:
    latest = latest_calibration_layout(store_id)
    if latest:
        return latest
    return STORE2_LAYOUT if store_id == "STORE_BLR_003" and STORE2_LAYOUT.exists() else DEFAULT_STORE_LAYOUT


def safe_layout_path(raw_path: str | None, store_id: str | None = None) -> Path:
    if not raw_path:
        return default_layout_for_store(store_id)
    path = Path(raw_path).expanduser().resolve()
    for root in LAYOUT_ROOTS:
        try:
            path.relative_to(root)
            if path.exists() and path.is_file():
                return path
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="layout path is outside allowed roots")


def normalize_camera_role(role: str | None) -> str:
    cleaned = (role or "zone").strip().lower().replace(" ", "_")
    aliases = {
        "staff_area": "staff-area",
        "staff-area": "staff-area",
        "detection": "detection_only",
        "detect": "detection_only",
    }
    normalized = aliases.get(cleaned, cleaned)
    if normalized not in ALLOWED_CAMERA_ROLES:
        raise HTTPException(status_code=400, detail=f"unsupported camera role: {role}")
    return normalized


def normalize_analysis_mode(mode: str | None) -> str:
    normalized = (mode or "full_store").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in ANALYSIS_MODE_LABELS:
        raise HTTPException(status_code=400, detail=f"unsupported analysis mode: {mode}")
    return normalized


def read_layout(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def read_calibration_registry() -> dict[str, Any]:
    if not CALIBRATION_REGISTRY_PATH.exists():
        return {"versions": []}
    try:
        payload = json.loads(CALIBRATION_REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"versions": []}
    versions = payload.get("versions") if isinstance(payload, dict) else None
    return {"versions": versions if isinstance(versions, list) else []}


def write_calibration_registry(payload: dict[str, Any]) -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CALIBRATION_REGISTRY_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(CALIBRATION_REGISTRY_PATH)


def register_calibration_version(version: dict[str, Any]) -> None:
    payload = read_calibration_registry()
    versions = [
        item
        for item in payload["versions"]
        if item.get("version_id") != version["version_id"]
    ]
    versions.append(version)
    payload["versions"] = versions[-200:]
    write_calibration_registry(payload)


def list_calibration_versions(store_id: str | None = None, camera_id: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for item in read_calibration_registry()["versions"]:
        if store_id and item.get("store_id") != store_id:
            continue
        if camera_id and item.get("camera_id") != camera_id:
            continue
        layout_path = Path(item.get("layout_path") or "")
        if not layout_path.exists():
            continue
        rows.append(item)
    return sorted(rows, key=lambda item: item.get("created_at") or "", reverse=True)


def latest_calibration_layout(store_id: str | None = None, camera_id: str | None = None) -> Path | None:
    versions = list_calibration_versions(store_id=store_id, camera_id=camera_id)
    if not versions and camera_id:
        versions = list_calibration_versions(store_id=store_id)
    if not versions:
        return None
    return Path(versions[0]["layout_path"])


def validate_normalized_point(point: list[float], label: str) -> list[float]:
    if len(point) != 2:
        raise HTTPException(status_code=400, detail=f"{label} must contain [x, y]")
    x, y = float(point[0]), float(point[1])
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise HTTPException(status_code=400, detail=f"{label} must be normalized between 0 and 1")
    return [round(x, 6), round(y, 6)]


def validate_polygon(points: list[list[float]], zone_id: str) -> list[list[float]]:
    if len(points) < 3:
        raise HTTPException(status_code=400, detail=f"{zone_id} needs at least 3 points")
    normalized = [validate_normalized_point(point, f"{zone_id} point {index + 1}") for index, point in enumerate(points)]
    unique_points = {tuple(point) for point in normalized}
    if len(unique_points) < 3:
        raise HTTPException(status_code=400, detail=f"{zone_id} needs at least 3 unique points")
    return normalized


def validate_entry_line(points: list[list[float]] | None) -> list[list[float]] | None:
    if points is None:
        return None
    if len(points) != 2:
        raise HTTPException(status_code=400, detail="entry line needs exactly 2 points")
    return [validate_normalized_point(point, f"entry line point {index + 1}") for index, point in enumerate(points)]


def calibration_version_id(store_id: str, camera_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"CAL_{store_id}_{camera_id}_{timestamp}_{uuid.uuid4().hex[:6]}".replace(" ", "_")


def extract_reference_frame(source: dict[str, Any], timestamp_seconds: float) -> dict[str, Any]:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenCV is required for frame extraction: {exc}") from exc

    if source.get("kind") == "rtsp":
        raise HTTPException(status_code=400, detail="reference-frame extraction requires a saved video file")
    video_path = safe_media_path(str(source.get("video_path")))
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise HTTPException(status_code=400, detail="video could not be opened")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        target_frame = int(timestamp_seconds * fps) if fps > 0 else 0
        if frame_count:
            target_frame = max(0, min(target_frame, frame_count - 1))
        capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ok, frame = capture.read()
        if not ok:
            raise HTTPException(status_code=400, detail="could not read reference frame")
        height, width = frame.shape[:2]
        CALIBRATION_FRAME_DIR.mkdir(parents=True, exist_ok=True)
        safe_source = str(source.get("source_id") or video_path.stem).replace("/", "_").replace("\\", "_")
        output_path = CALIBRATION_FRAME_DIR / f"{safe_source}_{source.get('camera_id')}_t{int(timestamp_seconds)}s.jpg"
        if not cv2.imwrite(str(output_path), frame):
            raise HTTPException(status_code=500, detail="could not write reference frame")
        return {
            "reference_frame_path": str(output_path),
            "reference_frame_url": media_url(output_path),
            "timestamp_seconds": round(target_frame / fps, 3) if fps else 0.0,
            "target_frame": target_frame,
            "frame_width": width,
            "frame_height": height,
            "source": source,
        }
    finally:
        capture.release()


def save_calibration_layout(request: CalibrationSaveRequest) -> dict[str, Any]:
    role = normalize_camera_role(request.camera_role)
    base_path = safe_layout_path(request.base_layout_path, request.store_id)
    layout = read_layout(base_path)
    if not layout:
        raise HTTPException(status_code=400, detail="base layout could not be read")

    zones = [
        {
            "zone_id": zone.zone_id.strip().upper().replace(" ", "_"),
            "label": zone.label.strip(),
            "kind": zone.kind.strip() or "product_zone",
            "sku_zone": zone.sku_zone.strip() if zone.sku_zone else None,
            "polygon_normalized": validate_polygon(zone.polygon_normalized, zone.zone_id),
        }
        for zone in request.zones
    ]
    entry_line = validate_entry_line(request.entry_line_normalized)
    if not zones and not entry_line:
        raise HTTPException(status_code=400, detail="save at least one zone polygon or entry line")

    cameras = layout.setdefault("cameras", [])
    camera = next((item for item in cameras if item.get("camera_id") == request.camera_id), None)
    if not camera:
        camera = {
            "camera_id": request.camera_id,
            "role": role,
            "description": f"User calibrated {request.camera_id}",
            "zones_normalized": {},
        }
        cameras.append(camera)
    camera["role"] = role if role != "detection_only" else camera.get("role", "zone")
    camera.setdefault("zones_normalized", {})
    for zone in zones:
        camera["zones_normalized"][zone["zone_id"]] = zone["polygon_normalized"]
    if entry_line:
        camera["entry_line_normalized"] = entry_line

    catalog = layout.setdefault("zones", [])
    catalog_by_id = {zone.get("zone_id"): zone for zone in catalog}
    for zone in zones:
        record = catalog_by_id.get(zone["zone_id"])
        if not record:
            record = {
                "zone_id": zone["zone_id"],
                "label": zone["label"],
                "kind": zone["kind"],
                "camera_ids": [],
                "sku_zone": zone["sku_zone"],
            }
            catalog.append(record)
            catalog_by_id[zone["zone_id"]] = record
        record["label"] = zone["label"]
        record["kind"] = zone["kind"]
        record["sku_zone"] = zone["sku_zone"]
        camera_ids = set(record.get("camera_ids") or [])
        camera_ids.add(request.camera_id)
        record["camera_ids"] = sorted(camera_ids)

    version_id = calibration_version_id(request.store_id, request.camera_id)
    CALIBRATION_LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CALIBRATION_LAYOUT_DIR / f"{version_id}.json"
    layout["calibration_version"] = version_id
    layout["calibration_metadata"] = {
        "version_id": version_id,
        "created_at": now_iso(),
        "base_layout_path": str(base_path),
        "source_id": request.source_id,
        "source_label": request.source_label,
        "reference_frame_path": request.reference_frame_path,
        "camera_id": request.camera_id,
        "camera_role": role,
        "notes": request.notes,
    }
    output_path.write_text(json.dumps(layout, indent=2), encoding="utf-8")

    version = {
        "version_id": version_id,
        "store_id": request.store_id,
        "camera_id": request.camera_id,
        "camera_role": role,
        "created_at": layout["calibration_metadata"]["created_at"],
        "layout_path": str(output_path),
        "base_layout_path": str(base_path),
        "source_id": request.source_id,
        "source_label": request.source_label,
        "reference_frame_path": request.reference_frame_path,
        "zone_count": len(zones),
        "zone_ids": [zone["zone_id"] for zone in zones],
        "has_entry_line": bool(entry_line),
        "notes": request.notes,
    }
    register_calibration_version(version)
    return {"version": version, "layout": layout}


def camera_capabilities(layout: dict[str, Any] | None, camera_id: str, role: str) -> set[str]:
    capabilities = {"detection_only"}
    if not layout:
        return capabilities
    cameras = {camera.get("camera_id"): camera for camera in layout.get("cameras", [])}
    camera = cameras.get(camera_id)
    if not camera:
        return capabilities
    zones = camera.get("zones_normalized") or {}
    if zones:
        capabilities.add("zone")
    if role == "entry" or camera.get("entry_line_normalized") or "ENTRY_EXIT" in zones:
        capabilities.add("entry_exit")
    if role == "billing" or "BILLING_COUNTER" in zones:
        capabilities.add("billing")
    if capabilities - {"detection_only"}:
        capabilities.add("full_store")
    return capabilities


def preferred_mode_for_role(role: str, capabilities: set[str]) -> str:
    for candidate in {
        "billing": ["billing", "zone"],
        "entry": ["entry_exit", "zone"],
        "zone": ["zone"],
        "staff-area": ["zone"],
    }.get(role, []):
        if candidate in capabilities:
            return candidate
    return "detection_only"


def layout_readiness(layout_path: Path, camera_id: str, role: str, requested_mode: str = "full_store") -> dict[str, Any]:
    layout = read_layout(layout_path)
    cameras = {camera.get("camera_id"): camera for camera in (layout or {}).get("cameras", [])}
    camera = cameras.get(camera_id)
    capabilities = camera_capabilities(layout, camera_id, role)
    effective_mode = requested_mode if requested_mode in capabilities else preferred_mode_for_role(role, capabilities)
    warnings: list[str] = []
    if not layout:
        warnings.append("No usable store layout found. The run will use detection-only output.")
    elif not camera:
        warnings.append(f"{camera_id} is not configured in the selected layout. The run will use detection-only output.")
    elif requested_mode not in capabilities:
        warnings.append(f"{ANALYSIS_MODE_LABELS[requested_mode]} is not available for {camera_id}; using {ANALYSIS_MODE_LABELS[effective_mode]}.")

    return {
        "layout_path": str(layout_path),
        "layout_exists": bool(layout),
        "camera_configured": bool(camera),
        "camera_id": camera_id,
        "camera_role": role,
        "requested_analysis_mode": requested_mode,
        "effective_analysis_mode": effective_mode,
        "status": "ready" if requested_mode == effective_mode and effective_mode != "detection_only" else "detection_only" if effective_mode == "detection_only" else "limited",
        "supported_analysis_modes": sorted(capabilities, key=lambda item: list(ANALYSIS_MODE_LABELS).index(item)),
        "analysis_mode_labels": ANALYSIS_MODE_LABELS,
        "zone_count": len((camera or {}).get("zones_normalized") or {}),
        "has_entry_line": bool((camera or {}).get("entry_line_normalized")),
        "warnings": warnings,
    }


def inspect_video(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "readable": False,
        "filename": path.name,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
    }
    try:
        import cv2  # type: ignore
    except Exception as exc:
        metadata["readable"] = None
        metadata["warning"] = f"OpenCV unavailable for metadata inspection: {exc}"
        return metadata

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            metadata["error"] = "video could not be opened"
            return metadata
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        metadata.update(
            {
                "readable": True,
                "frame_count": frame_count,
                "fps": round(fps, 3) if fps else None,
                "width": width or None,
                "height": height or None,
                "duration_seconds": round(frame_count / fps, 3) if frame_count and fps else None,
            }
        )
        return metadata
    finally:
        capture.release()


def summarize_pos_transactions(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [row["timestamp"] for row in transactions]
    return {
        "transaction_count": len(transactions),
        "store_ids": sorted({row["store_id"] for row in transactions}),
        "window": {"start": min(timestamps) if timestamps else None, "end": max(timestamps) if timestamps else None},
        "total_sales_inr": round(sum(float(row.get("basket_value_inr", 0)) for row in transactions), 2),
        "sample": transactions[:10],
    }


def clean_mapping_value(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def has_pos_mapping(mapping: dict[str, str | None]) -> bool:
    return any(value for key, value in mapping.items() if key != "timezone_offset")


async def parse_dashboard_pos_upload(
    file: UploadFile,
    store_id: str,
    mapping: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    if Path(file.filename).suffix.lower() != ".csv":
        raise HTTPException(status_code=400, detail="POS upload must be a CSV file")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as handle:
        temp_path = Path(handle.name)
        while chunk := await file.read(1024 * 1024):
            handle.write(chunk)
    try:
        clean_mapping = mapping or {}
        if has_pos_mapping(clean_mapping):
            rows = parse_mapped_csv(
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
            rows = parse_purplle_csv(temp_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"POS parse failed: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
        await file.close()
    for row in rows:
        row["store_id"] = store_id
    return rows


def session_progress(stage: str, status: str = "queued", error: str | None = None) -> dict[str, Any]:
    stage_aliases = {
        "yolo_detection": "detection",
        "api_replay": "events",
        "report_building": "report",
        "failed": "complete",
    }
    normalized_stage = stage_aliases.get(stage, stage)
    current_index = next((index for index, (key, _) in enumerate(PROGRESS_STEPS) if key == normalized_stage), 1)
    failed = status in {"error", "failed"} or bool(error)
    steps = []
    for index, (key, label) in enumerate(PROGRESS_STEPS):
        if failed and index == current_index:
            step_status = "failed"
        elif status == "complete" or index < current_index:
            step_status = "complete"
        elif index == current_index:
            step_status = "current"
        else:
            step_status = "pending"
        steps.append({"key": key, "label": label, "status": step_status})
    percent_by_stage = {
        "upload": 10,
        "queued": 20,
        "detection": 45,
        "events": 70,
        "pos": 82,
        "report": 92,
        "complete": 100,
    }
    return {
        "stage": normalized_stage,
        "label": dict(PROGRESS_STEPS).get(normalized_stage, normalized_stage),
        "percent": 100 if status == "complete" else percent_by_stage.get(normalized_stage, 20),
        "steps": steps,
        "error": error,
    }


def media_url(path: Path) -> str:
    return f"/dashboard/media?path={quote(str(path), safe='')}"


def source_id_for(path: Path, camera_id: str) -> str:
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"{path}:{camera_id}").hex[:10]
    return f"{camera_id.lower()}_{digest}"


def source_payload(
    path: Path,
    store_id: str,
    camera_id: str,
    role: str,
    layout_path: Path,
    label: str | None = None,
    source_id: str | None = None,
    created_at: str | None = None,
    session_label: str | None = None,
    original_filename: str | None = None,
    video_metadata: dict[str, Any] | None = None,
    requested_analysis_mode: str = "full_store",
) -> dict[str, Any]:
    clean_role = normalize_camera_role(role)
    clean_mode = normalize_analysis_mode(requested_analysis_mode)
    readiness = layout_readiness(layout_path, camera_id, clean_role, clean_mode)
    tracking_profile = profile_for_camera(store_id=store_id, camera_id=camera_id, role=clean_role)
    metadata = video_metadata or {
        "readable": None,
        "filename": path.name,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
    }
    return {
        "source_id": source_id or source_id_for(path, camera_id),
        "label": label or f"{store_id} {camera_id} - {role}",
        "store_id": store_id,
        "camera_id": camera_id,
        "role": clean_role,
        "kind": "file",
        "video_path": str(path),
        "layout_path": str(layout_path),
        "media_url": media_url(path),
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "created_at": created_at,
        "session_label": session_label,
        "original_filename": original_filename or path.name,
        "video_metadata": metadata,
        "layout_readiness": readiness,
        "analysis_capabilities": readiness["supported_analysis_modes"],
        "recommended_analysis_mode": readiness["effective_analysis_mode"],
        "tracking_profile": tracking_profile,
    }


def read_upload_registry() -> dict[str, Any]:
    if not UPLOAD_REGISTRY_PATH.exists():
        return {"sources": []}
    try:
        payload = json.loads(UPLOAD_REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"sources": []}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    return {"sources": sources if isinstance(sources, list) else []}


def write_upload_registry(payload: dict[str, Any]) -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = UPLOAD_REGISTRY_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(UPLOAD_REGISTRY_PATH)


def register_upload_source(source: dict[str, Any]) -> None:
    payload = read_upload_registry()
    sources = [
        item
        for item in payload["sources"]
        if item.get("source_id") != source["source_id"] and item.get("video_path") != source["video_path"]
    ]
    sources.append(source)
    payload["sources"] = sources[-200:]
    write_upload_registry(payload)


def upload_registry_sources() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in read_upload_registry()["sources"]:
        try:
            path = safe_media_path(str(item.get("video_path", "")))
            store_id = str(item.get("store_id") or "STORE_UPLOAD")
            camera_id = str(item.get("camera_id") or "CAM_1")
            try:
                layout_path = latest_calibration_layout(store_id, camera_id) or latest_calibration_layout(store_id) or safe_layout_path(item.get("layout_path"), store_id)
            except HTTPException:
                layout_path = default_layout_for_store(store_id)
            rows.append(
                source_payload(
                    path=path,
                    store_id=store_id,
                    camera_id=camera_id,
                    role=str(item.get("role") or "uploaded"),
                    layout_path=layout_path,
                    label=item.get("label"),
                    source_id=item.get("source_id"),
                    created_at=item.get("created_at"),
                    session_label=item.get("session_label"),
                    original_filename=item.get("original_filename"),
                    video_metadata=item.get("video_metadata"),
                    requested_analysis_mode=str(item.get("requested_analysis_mode") or "full_store"),
                )
            )
        except Exception:
            continue
    return rows


def add_source(sources: list[dict[str, Any]], path: Path, store_id: str, camera_id: str, role: str, layout_path: Path) -> None:
    if not path.exists():
        return
    effective_layout = latest_calibration_layout(store_id, camera_id) or latest_calibration_layout(store_id) or layout_path
    sources.append(source_payload(path, store_id, camera_id, role, effective_layout))


def dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    seen_saved_labels: set[tuple[str, str, str, str]] = set()
    for source in sources:
        source_id = str(source.get("source_id") or "")
        if source_id and source_id in seen_source_ids:
            continue
        if source_id:
            seen_source_ids.add(source_id)

        label_key = (
            str(source.get("store_id") or ""),
            str(source.get("camera_id") or ""),
            str(source.get("role") or ""),
            str(source.get("label") or ""),
        )
        if not source.get("created_at") and label_key in seen_saved_labels:
            continue
        if not source.get("created_at"):
            seen_saved_labels.add(label_key)
        deduped.append(source)
    return deduped


def known_sources() -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    store1_layout = DEFAULT_STORE_LAYOUT
    store2_layout = STORE2_LAYOUT

    original_dir = ORIGINAL_MEDIA_DIR / "CCTV Footage"
    for idx, role in {
        1: "zone",
        2: "zone",
        3: "entry",
        4: "staff-area",
        5: "billing",
    }.items():
        add_source(sources, original_dir / f"CAM {idx}.mp4", "STORE_BLR_002", f"CAM_{idx}", role, store1_layout)

    renamed_dir = ORIGINAL_MEDIA_DIR / "Store 1"
    for filename, camera_id, role in [
        ("CAM 1 - zone.mp4", "CAM_1", "zone"),
        ("CAM 2 - zone.mp4", "CAM_2", "zone"),
        ("CAM 3 - entry.mp4", "CAM_3", "entry"),
        ("CAM 5 - billing.mp4", "CAM_5", "billing"),
    ]:
        add_source(sources, renamed_dir / filename, "STORE_BLR_002", camera_id, role, store1_layout)

    store2_dir = ORIGINAL_MEDIA_DIR / "Store 2"
    for filename, camera_id, role in [
        ("entry 1.mp4", "CAM_1", "entry"),
        ("entry 2.mp4", "CAM_2", "entry"),
        ("zone.mp4", "CAM_3", "zone"),
        ("billing_area.mp4", "CAM_4", "billing"),
    ]:
        add_source(sources, store2_dir / filename, "STORE_BLR_003", camera_id, role, store2_layout)

    registered_uploads = upload_registry_sources()
    sources.extend(registered_uploads)
    registered_paths = {Path(source["video_path"]).resolve() for source in registered_uploads}

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(UPLOAD_DIR.glob("*")):
        if (
            path.is_file()
            and path.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS
            and path.resolve() not in registered_paths
        ):
            add_source(sources, path, "STORE_UPLOAD", "CAM_1", "uploaded", store1_layout)

    return dedupe_sources(sources)


def resolve_source(request: LiveSessionRequest) -> dict[str, Any]:
    sources = {source["source_id"]: source for source in known_sources()}
    source = sources.get(request.source_id or "") if request.source_id else None
    requested_role = normalize_camera_role(request.camera_role or (source or {}).get("role") or "zone")
    requested_mode = normalize_analysis_mode(request.analysis_mode)

    if request.rtsp_url:
        layout_path = safe_layout_path(request.layout_path, request.store_id)
        camera_id = request.camera_id or "CAM_1"
        store_id = request.store_id or f"LIVE_RTSP_{uuid.uuid4().hex[:6].upper()}"
        readiness = layout_readiness(layout_path, camera_id, requested_role, requested_mode)
        return {
            "kind": "rtsp",
            "label": request.session_label or request.rtsp_url,
            "video_path": request.rtsp_url,
            "media_url": None,
            "camera_id": camera_id,
            "role": requested_role if request.camera_role else "live",
            "store_id": store_id,
            "layout_path": str(layout_path),
            "session_label": request.session_label,
            "layout_readiness": readiness,
            "analysis_capabilities": readiness["supported_analysis_modes"],
            "recommended_analysis_mode": readiness["effective_analysis_mode"],
            "tracking_profile": profile_for_camera(store_id=store_id, camera_id=camera_id, role=requested_role),
        }

    if request.video_path:
        path = safe_media_path(request.video_path)
        store_id = request.store_id or f"LIVE_FILE_{uuid.uuid4().hex[:6].upper()}"
        layout_path = safe_layout_path(request.layout_path, store_id)
        camera_id = request.camera_id or "CAM_1"
        readiness = layout_readiness(layout_path, camera_id, requested_role, requested_mode)
        return {
            "kind": "file",
            "label": request.session_label or path.name,
            "video_path": str(path),
            "media_url": media_url(path),
            "camera_id": camera_id,
            "role": requested_role if request.camera_role else "uploaded",
            "store_id": store_id,
            "layout_path": str(layout_path),
            "session_label": request.session_label,
            "layout_readiness": readiness,
            "analysis_capabilities": readiness["supported_analysis_modes"],
            "recommended_analysis_mode": readiness["effective_analysis_mode"],
            "tracking_profile": profile_for_camera(store_id=store_id, camera_id=camera_id, role=requested_role),
        }

    if source:
        source = dict(source)
        source["store_id"] = request.store_id or source["store_id"]
        source["camera_id"] = request.camera_id or source["camera_id"]
        source["role"] = requested_role or source.get("role") or "uploaded"
        preferred_layout = (
            request.layout_path
            or latest_calibration_layout(source["store_id"], source["camera_id"])
            or latest_calibration_layout(source["store_id"])
            or source.get("layout_path")
        )
        layout_path = safe_layout_path(preferred_layout, source["store_id"])
        readiness = layout_readiness(layout_path, source["camera_id"], source["role"], requested_mode)
        source["layout_path"] = str(layout_path)
        source["layout_readiness"] = readiness
        source["analysis_capabilities"] = readiness["supported_analysis_modes"]
        source["recommended_analysis_mode"] = readiness["effective_analysis_mode"]
        source["tracking_profile"] = profile_for_camera(store_id=source["store_id"], camera_id=source["camera_id"], role=source["role"])
        source["session_label"] = request.session_label or source.get("session_label")
        if request.session_label:
            source["label"] = request.session_label
        return source

    raise HTTPException(status_code=400, detail="Provide source_id, video_path, or rtsp_url")


def update_session(session_id: str, **updates: Any) -> dict[str, Any]:
    with SESSION_LOCK:
        session = SESSIONS[session_id]
        session.update(updates)
        session["updated_at"] = now_iso()
        return dict(session)


def update_backend_session(session_id: str, **updates: Any) -> None:
    try:
        post_json(f"/sessions/{quote(session_id, safe='')}/status", updates)
    except Exception:
        # Dashboard session state remains the source of truth for UI recovery.
        pass


def get_session_or_404(session_id: str) -> dict[str, Any]:
    with SESSION_LOCK:
        session = SESSIONS.get(session_id)
        if session:
            return dict(session)
    session = recovered_dashboard_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    with SESSION_LOCK:
        SESSIONS.setdefault(session_id, session)
    return dict(session)


def source_for_backend_session(backend_session: dict[str, Any]) -> dict[str, Any]:
    metadata = backend_session.get("metadata") or {}
    source_id = backend_session.get("source_id")
    video_path = backend_session.get("video_path")
    for source in known_sources():
        if source_id and source.get("source_id") == source_id:
            return dict(source)
        if video_path and source.get("video_path") == video_path:
            return dict(source)

    store_id = backend_session.get("store_id") or "STORE_UNKNOWN"
    camera_id = backend_session.get("camera_id") or "CAM_1"
    role = metadata.get("camera_role") or "uploaded"
    layout_path = default_layout_for_store(store_id)
    source = {
        "source_id": source_id,
        "label": backend_session.get("source_label") or metadata.get("session_label") or backend_session.get("session_id"),
        "store_id": store_id,
        "camera_id": camera_id,
        "role": normalize_camera_role(role),
        "kind": backend_session.get("source_type") or "file",
        "video_path": video_path,
        "layout_path": str(layout_path),
        "media_url": None,
        "session_label": metadata.get("session_label"),
        "layout_readiness": layout_readiness(layout_path, camera_id, normalize_camera_role(role), metadata.get("analysis_mode_requested") or "full_store"),
        "analysis_capabilities": [],
        "recommended_analysis_mode": metadata.get("analysis_mode_effective") or "detection_only",
        "tracking_profile": profile_for_camera(store_id=store_id, camera_id=camera_id, role=role),
    }
    if video_path:
        try:
            path = safe_media_path(video_path)
            source["media_url"] = media_url(path)
        except HTTPException:
            source["media_url"] = None
    return source


def dashboard_session_from_backend(backend_session: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(backend_session, dict) or not backend_session.get("session_id"):
        return None
    session_id = str(backend_session["session_id"])
    metadata = backend_session.get("metadata") or {}
    source = source_for_backend_session(backend_session)
    session_dir = LIVE_SESSION_DIR / session_id
    events_path = session_dir / "events.jsonl"
    overlay_path = session_dir / "overlays.jsonl"
    overlay_frames = read_overlay_jsonl(overlay_path, limit=0) if overlay_path.exists() else []
    event_count = int(backend_session.get("event_count") or 0)
    tracking_profile = metadata.get("tracking_profile") or source.get("tracking_profile") or {}
    tracker_backend = metadata.get("tracker_backend") or tracking_profile.get("tracker_backend") or "auto"
    progress = session_progress("complete", backend_session.get("status") or "complete")

    return {
        "session_id": session_id,
        "backend_session": backend_session,
        "status": backend_session.get("status") or "complete",
        "stage": "recovered",
        "created_at": backend_session.get("created_at") or now_iso(),
        "updated_at": backend_session.get("completed_at") or backend_session.get("created_at") or now_iso(),
        "store_id": backend_session.get("store_id") or source.get("store_id"),
        "camera_id": backend_session.get("camera_id") or source.get("camera_id"),
        "camera_role": metadata.get("camera_role") or source.get("role"),
        "analysis_mode": metadata.get("analysis_mode_effective") or source.get("recommended_analysis_mode"),
        "requested_analysis_mode": metadata.get("analysis_mode_requested"),
        "layout_readiness": metadata.get("layout_readiness") or source.get("layout_readiness") or {},
        "session_label": metadata.get("session_label") or backend_session.get("source_label") or source.get("label"),
        "source": source,
        "max_seconds": metadata.get("max_seconds"),
        "process_fps": metadata.get("process_fps"),
        "replay_speed": metadata.get("replay_speed"),
        "min_area": metadata.get("min_area"),
        "yolo": {
            "conf": metadata.get("yolo_conf"),
            "iou": metadata.get("yolo_iou"),
            "imgsz": metadata.get("yolo_imgsz"),
            "model_path": str(DEFAULT_MODEL_PATH),
            "tracker_backend": tracker_backend,
        },
        "tracking_profile": tracking_profile,
        "auto_profile_applied": metadata.get("auto_profile_applied", False),
        "generated_events": event_count,
        "replayed_events": event_count,
        "inserted_events": event_count,
        "duplicate_events": 0,
        "events_path": str(events_path) if events_path.exists() else None,
        "overlay_path": str(overlay_path) if overlay_path.exists() else None,
        "overlay_status": "ready" if overlay_frames else "missing",
        "overlay_frames": len(overlay_frames),
        "overlay_tracks": sum(len(frame.get("tracks", [])) for frame in overlay_frames),
        "progress": progress,
        "last_events": [],
        "error": backend_session.get("error"),
        "recovered": True,
    }


def recovered_dashboard_session(session_id: str) -> dict[str, Any] | None:
    try:
        backend_payload = fetch_json(f"/sessions/{quote(session_id, safe='')}")
    except Exception:
        return None
    backend_session = backend_payload.get("session") or backend_payload
    return dashboard_session_from_backend(backend_session)


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def overlay_geometry_for_session(session: dict[str, Any]) -> dict[str, Any]:
    source = session.get("source") or {}
    layout_path_value = source.get("layout_path")
    camera_id = session.get("camera_id") or source.get("camera_id")
    if not layout_path_value:
        return {"zones": [], "entry_line_normalized": None}
    layout_path = Path(layout_path_value)
    if not layout_path.exists() or not camera_id:
        return {"zones": [], "entry_line_normalized": None}

    layout = json.loads(layout_path.read_text(encoding="utf-8-sig"))
    labels = {
        zone.get("zone_id"): {
            "label": zone.get("label") or zone.get("zone_id"),
            "kind": zone.get("kind"),
            "sku_zone": zone.get("sku_zone"),
        }
        for zone in layout.get("zones", [])
    }
    cameras = {camera.get("camera_id"): camera for camera in layout.get("cameras", [])}
    camera = cameras.get(camera_id, {})
    zones = []
    for zone_id, polygon in (camera.get("zones_normalized") or {}).items():
        metadata = labels.get(zone_id, {})
        zones.append(
            {
                "zone_id": zone_id,
                "label": metadata.get("label") or zone_id,
                "kind": metadata.get("kind"),
                "sku_zone": metadata.get("sku_zone"),
                "polygon_normalized": polygon,
            }
        )
    return {
        "zones": zones,
        "entry_line_normalized": camera.get("entry_line_normalized"),
    }


def overlay_payload_for_session(session: dict[str, Any], limit: int = 5000) -> dict[str, Any]:
    overlay_path = session.get("overlay_path")
    path = Path(overlay_path) if overlay_path else None
    frames = read_overlay_jsonl(path, limit=limit) if path else []
    track_count = sum(len(frame.get("tracks", [])) for frame in frames)
    visitor_ids = {
        track.get("visitor_id") or str(track.get("track_id"))
        for frame in frames
        for track in frame.get("tracks", [])
        if track.get("visitor_id") or track.get("track_id") is not None
    }
    staff_ids = {
        track.get("visitor_id") or str(track.get("track_id"))
        for frame in frames
        for track in frame.get("tracks", [])
        if track.get("is_staff") and (track.get("visitor_id") or track.get("track_id") is not None)
    }
    countable_ids = {
        track.get("visitor_id") or str(track.get("track_id"))
        for frame in frames
        for track in frame.get("tracks", [])
        if track.get("countable", True) and (track.get("visitor_id") or track.get("track_id") is not None)
    }
    ever_suspect_ids = {
        track.get("visitor_id") or str(track.get("track_id"))
        for frame in frames
        for track in frame.get("tracks", [])
        if track.get("countable") is False and (track.get("visitor_id") or track.get("track_id") is not None)
    }
    suspect_ids = ever_suspect_ids - countable_ids
    events_path_value = session.get("events_path")
    if not events_path_value and session.get("session_id"):
        candidate = LIVE_SESSION_DIR / str(session["session_id"]) / "events.jsonl"
        events_path_value = str(candidate) if candidate.exists() else None
    event_rows = read_events(Path(events_path_value)) if events_path_value else []
    counted_visitor_ids = {
        event.get("visitor_id")
        for event in event_rows
        if event.get("visitor_id") and not event.get("is_staff", False)
    }
    overlay_only_countable_ids = countable_ids - counted_visitor_ids
    frame_width = next((frame.get("frame_width") for frame in frames if frame.get("frame_width")), None)
    frame_height = next((frame.get("frame_height") for frame in frames if frame.get("frame_height")), None)
    video_times = [
        float(frame.get("video_time_seconds", 0))
        for frame in frames
        if frame.get("video_time_seconds") is not None
    ]
    first_video_time = min(video_times) if video_times else None
    last_video_time = max(video_times) if video_times else None
    sample_interval = None
    if len(frames) >= 2:
        sample_interval = round(
            max(0.0, float(frames[1].get("video_time_seconds", 0)) - float(frames[0].get("video_time_seconds", 0))),
            3,
        )

    return {
        "session_id": session.get("session_id"),
        "camera_id": session.get("camera_id"),
        "available": bool(frames),
        "status": "ready" if frames else "building",
        "frame_count": len(frames),
        "track_count": track_count,
        "unique_track_count": len(visitor_ids),
        "staff_track_count": len(staff_ids),
        "person_track_count": len(countable_ids),
        "valid_person_track_count": len(countable_ids),
        "counted_person_track_count": len(counted_visitor_ids),
        "overlay_only_countable_track_count": len(overlay_only_countable_ids),
        "suspect_track_count": len(suspect_ids),
        "transient_track_count": len(ever_suspect_ids & countable_ids),
        "frame_width": frame_width,
        "frame_height": frame_height,
        "first_video_time_seconds": first_video_time,
        "last_video_time_seconds": last_video_time,
        "coverage_seconds": round(last_video_time - first_video_time, 3) if first_video_time is not None and last_video_time is not None else None,
        "sample_interval_seconds": sample_interval,
        "geometry": overlay_geometry_for_session(session),
        "frames": frames,
    }


def parse_event_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def replay_events(session_id: str, events: list[dict[str, Any]], replay_speed: float, stop_event: threading.Event) -> None:
    previous_ts: datetime | None = None
    inserted = 0
    duplicates = 0
    last_events: list[dict[str, Any]] = []

    for event in events:
        if stop_event.is_set():
            update_session(session_id, status="stopped", progress=session_progress("complete", "stopped"))
            update_backend_session(session_id, status="stopped", completed_at=now_iso())
            return

        current_ts = parse_event_ts(event["timestamp"])
        if previous_ts is not None:
            sleep_seconds = max(0.0, (current_ts - previous_ts).total_seconds() / replay_speed)
            time.sleep(min(sleep_seconds, 3.0))
        previous_ts = current_ts

        event.setdefault("session_id", session_id)
        result = post_json("/events/ingest", {"session_id": session_id, "events": [event]})
        inserted += int(result.get("inserted", 0))
        duplicates += int(result.get("duplicates", 0))
        last_events = (last_events + [event])[-20:]
        update_session(
            session_id,
            status="replaying",
            inserted_events=inserted,
            duplicate_events=duplicates,
            replayed_events=inserted + duplicates,
            last_event=event,
            last_events=last_events,
        )


def run_session_worker(session_id: str, source: dict[str, Any], request: LiveSessionRequest, stop_event: threading.Event) -> None:
    from pipeline.detect import run_detection

    session_dir = LIVE_SESSION_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"
    overlay_path = session_dir / "overlays.jsonl"

    try:
        update_session(
            session_id,
            status="processing",
            stage="yolo_detection",
            events_path=str(events_path),
            overlay_path=str(overlay_path),
            overlay_status="building",
            progress=session_progress("detection", "processing"),
        )
        update_backend_session(session_id, status="processing", started_at=now_iso())
        event_count = run_detection(
            video_path=source["video_path"],
            camera_id=source["camera_id"],
            layout_path=Path(source["layout_path"]),
            time_offsets_path=DEFAULT_TIME_OFFSETS,
            output_path=events_path,
            store_id=source["store_id"],
            session_id=session_id,
            process_fps=request.process_fps,
            max_seconds=request.max_seconds,
            min_area=request.min_area,
            yolo_conf=request.yolo_conf,
            yolo_iou=request.yolo_iou,
            yolo_imgsz=request.yolo_imgsz,
            yolo_model_path=DEFAULT_MODEL_PATH,
            overlay_path=overlay_path,
            tracker_backend=request.tracker_backend,
        )

        events = read_events(events_path)
        overlay_frames = read_overlay_jsonl(overlay_path, limit=0)
        update_session(
            session_id,
            status="replaying",
            stage="api_replay",
            generated_events=event_count,
            replayed_events=0,
            overlay_status="ready",
            overlay_frames=len(overlay_frames),
            overlay_tracks=sum(len(frame.get("tracks", [])) for frame in overlay_frames),
            progress=session_progress("events", "replaying"),
        )
        update_backend_session(session_id, status="replaying", event_count=event_count)
        replay_events(session_id, events, request.replay_speed, stop_event)
        if not stop_event.is_set():
            update_session(
                session_id,
                status="complete",
                stage="complete",
                completed_at=now_iso(),
                progress=session_progress("complete", "complete"),
            )
            update_backend_session(session_id, status="complete", completed_at=now_iso())
    except Exception as exc:
        update_session(
            session_id,
            status="error",
            error=str(exc),
            stage="failed",
            overlay_status="failed",
            progress=session_progress("failed", "failed", str(exc)),
        )
        update_backend_session(session_id, status="failed", error=str(exc), completed_at=now_iso())


@app.get("/")
async def get_index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "api_base_url": API_BASE_URL}


@app.get("/dashboard/sources")
async def get_sources() -> dict[str, Any]:
    return {"sources": known_sources()}


@app.get("/dashboard/stores")
async def get_store_registry() -> dict[str, Any]:
    return fetch_json("/stores")


@app.get("/dashboard/onboarding/validate")
async def validate_onboarding(
    store_id: str = "STORE_BLR_002",
    camera_id: str = "CAM_1",
    camera_role: str = "zone",
    analysis_mode: str = "full_store",
    layout_path: str = "",
) -> dict[str, Any]:
    role = normalize_camera_role(camera_role)
    mode = normalize_analysis_mode(analysis_mode)
    layout = safe_layout_path(layout_path or None, store_id)
    readiness = layout_readiness(layout, camera_id.strip() or "CAM_1", role, mode)
    versions = list_calibration_versions(store_id=store_id, camera_id=camera_id.strip() or "CAM_1")
    return {
        "store_id": store_id,
        "camera_id": camera_id.strip() or "CAM_1",
        "camera_role": role,
        "requested_analysis_mode": mode,
        "effective_analysis_mode": readiness["effective_analysis_mode"],
        "layout_readiness": readiness,
        "latest_calibration": versions[0] if versions else None,
        "ready": True,
        "warnings": readiness["warnings"],
    }


@app.get("/dashboard/calibrations")
async def get_calibrations(store_id: str | None = None, camera_id: str | None = None) -> dict[str, Any]:
    versions = list_calibration_versions(store_id=store_id, camera_id=camera_id)
    return {"count": len(versions), "versions": versions, "latest": versions[0] if versions else None}


@app.post("/dashboard/calibration/reference-frame")
async def create_calibration_reference_frame(request: CalibrationReferenceRequest) -> dict[str, Any]:
    role = normalize_camera_role(request.camera_role)
    source = resolve_source(
        LiveSessionRequest(
            source_id=request.source_id,
            video_path=request.video_path,
            store_id=request.store_id,
            camera_id=request.camera_id,
            camera_role=role,
            analysis_mode="detection_only",
        )
    )
    return extract_reference_frame(source, request.timestamp_seconds)


@app.post("/dashboard/calibration/save")
async def save_calibration(request: CalibrationSaveRequest) -> dict[str, Any]:
    result = save_calibration_layout(request)
    readiness = layout_readiness(
        Path(result["version"]["layout_path"]),
        request.camera_id,
        normalize_camera_role(request.camera_role),
        "full_store",
    )
    return {**result, "layout_readiness": readiness}


@app.post("/dashboard/upload")
async def upload_video(
    file: UploadFile = File(...),
    camera_id: str = Form("CAM_1"),
    store_id: str = Form("STORE_BLR_002"),
    camera_role: str = Form("zone"),
    session_label: str = Form(""),
    analysis_mode: str = Form("full_store"),
    layout_path: str = Form(""),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    suffix = Path(file.filename).suffix.lower() or ".mp4"
    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="unsupported video extension")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in file.filename)
    target = UPLOAD_DIR / f"{uuid.uuid4().hex[:10]}_{safe_name}"
    bytes_written = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"video is larger than {MAX_UPLOAD_MB} MB")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    clean_store_id = store_id.strip() or "STORE_BLR_002"
    clean_camera_id = camera_id.strip() or "CAM_1"
    clean_role = normalize_camera_role(camera_role)
    clean_mode = normalize_analysis_mode(analysis_mode)
    layout = safe_layout_path(layout_path or None, clean_store_id)
    clean_label = session_label.strip() or target.name
    video_metadata = inspect_video(target)
    if video_metadata.get("readable") is False:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=video_metadata.get("error") or "video could not be opened")
    source = source_payload(
        path=target,
        store_id=clean_store_id,
        camera_id=clean_camera_id,
        role=clean_role,
        layout_path=layout,
        label=clean_label,
        created_at=now_iso(),
        session_label=clean_label,
        original_filename=file.filename,
        video_metadata=video_metadata,
        requested_analysis_mode=clean_mode,
    )
    register_upload_source(source)
    return {
        "source": source,
        "onboarding": {
            "upload": "complete",
            "layout_readiness": source["layout_readiness"],
            "effective_analysis_mode": source["recommended_analysis_mode"],
            "video_metadata": video_metadata,
        },
    }


@app.get("/dashboard/media")
async def get_media(path: str) -> FileResponse:
    media_path = safe_media_path(path)
    return FileResponse(media_path)


@app.get("/dashboard/quality")
async def get_quality_report() -> dict[str, Any]:
    report_path = PROJECT_ROOT / "data" / "reports" / "usp_quality_report.json"
    if not report_path.exists():
        return {"available": False}
    return {"available": True, "report": json.loads(report_path.read_text(encoding="utf-8"))}


@app.get("/dashboard/stores/{store_id}/snapshot")
async def get_dashboard_store_snapshot(store_id: str, session_id: str | None = None) -> dict[str, Any]:
    return await fetch_store_snapshot(store_id, session_id=session_id)


@app.get("/dashboard/stores/{store_id}/events/search")
async def search_store_events(
    store_id: str,
    session_id: str | None = None,
    event_type: str | None = None,
    camera_id: str | None = None,
    visitor_id: str | None = None,
    zone_id: str | None = None,
    is_staff: bool | None = None,
    low_confidence: bool | None = None,
    limit: int = 50,
    sort: str = "desc",
) -> dict[str, Any]:
    query = {
        "limit": max(1, min(int(limit), 200)),
        "sort": sort,
    }
    for key, value in {
        "session_id": session_id,
        "event_type": event_type,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "zone_id": zone_id,
        "is_staff": is_staff,
        "low_confidence": low_confidence,
    }.items():
        if value is not None and value != "":
            query[key] = value
    path = f"/stores/{quote(store_id, safe='')}/events/search?{urlencode(query)}"
    return fetch_json(path)


def pos_mapping_payload(
    transaction_id_column: str | None,
    amount_column: str | None,
    timestamp_column: str | None,
    date_column: str | None,
    time_column: str | None,
    store_id_column: str | None,
    timezone_offset: str | None,
) -> dict[str, str | None]:
    return {
        "transaction_id_column": clean_mapping_value(transaction_id_column),
        "amount_column": clean_mapping_value(amount_column),
        "timestamp_column": clean_mapping_value(timestamp_column),
        "date_column": clean_mapping_value(date_column),
        "time_column": clean_mapping_value(time_column),
        "store_id_column": clean_mapping_value(store_id_column),
        "timezone_offset": clean_mapping_value(timezone_offset) or "+05:30",
    }


@app.post("/dashboard/stores/{store_id}/pos/preview")
async def dashboard_preview_pos_upload(
    store_id: str,
    file: UploadFile = File(...),
    transaction_id_column: str | None = Form(default=None),
    amount_column: str | None = Form(default=None),
    timestamp_column: str | None = Form(default=None),
    date_column: str | None = Form(default=None),
    time_column: str | None = Form(default=None),
    store_id_column: str | None = Form(default=None),
    timezone_offset: str | None = Form(default="+05:30"),
) -> dict[str, Any]:
    rows = await parse_dashboard_pos_upload(
        file,
        store_id,
        pos_mapping_payload(
            transaction_id_column,
            amount_column,
            timestamp_column,
            date_column,
            time_column,
            store_id_column,
            timezone_offset,
        ),
    )
    return {"store_id": store_id, "imported": False, **summarize_pos_transactions(rows)}


@app.post("/dashboard/stores/{store_id}/pos/import")
async def dashboard_import_pos_upload(
    store_id: str,
    file: UploadFile = File(...),
    transaction_id_column: str | None = Form(default=None),
    amount_column: str | None = Form(default=None),
    timestamp_column: str | None = Form(default=None),
    date_column: str | None = Form(default=None),
    time_column: str | None = Form(default=None),
    store_id_column: str | None = Form(default=None),
    timezone_offset: str | None = Form(default="+05:30"),
) -> dict[str, Any]:
    rows = await parse_dashboard_pos_upload(
        file,
        store_id,
        pos_mapping_payload(
            transaction_id_column,
            amount_column,
            timestamp_column,
            date_column,
            time_column,
            store_id_column,
            timezone_offset,
        ),
    )
    result = post_json(
        f"/stores/{quote(store_id, safe='')}/pos/import-json",
        {"transactions": rows},
    )
    return {"store_id": store_id, **result}


@app.get("/dashboard/stores/{store_id}/pos/matches")
async def dashboard_pos_matches(store_id: str, session_id: str | None = None) -> dict[str, Any]:
    query = f"?{urlencode({'session_id': session_id})}" if session_id else ""
    return fetch_json(f"/stores/{quote(store_id, safe='')}/pos/matches{query}")


@app.post("/dashboard/stores/{store_id}/visitors/{visitor_id}/staff-correction")
async def dashboard_staff_correction(
    store_id: str,
    visitor_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return post_json(
        f"/stores/{quote(store_id, safe='')}/visitors/{quote(visitor_id, safe='')}/staff-correction",
        payload,
    )


@app.get("/dashboard/stores/{store_id}/visitors/{visitor_id}/timeline")
async def get_dashboard_visitor_timeline(
    store_id: str,
    visitor_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    query = f"?{urlencode({'session_id': session_id})}" if session_id else ""
    path = f"/stores/{quote(store_id, safe='')}/visitors/{quote(visitor_id, safe='')}/timeline{query}"
    return fetch_json(path)


@app.get("/dashboard/sessions")
async def list_sessions() -> dict[str, Any]:
    with SESSION_LOCK:
        sessions_by_id = {session_id: dict(session) for session_id, session in SESSIONS.items()}

    try:
        backend_payload = await asyncio.to_thread(fetch_json, "/sessions?limit=50")
        for backend_session in backend_payload.get("sessions", []):
            session_id = backend_session.get("session_id")
            if not session_id or session_id in sessions_by_id:
                continue
            recovered = dashboard_session_from_backend(backend_session)
            if recovered:
                sessions_by_id[session_id] = recovered
    except Exception:
        pass

    sessions = sorted(
        sessions_by_id.values(),
        key=lambda row: row.get("created_at") or row.get("updated_at") or "",
        reverse=True,
    )
    return {"sessions": sessions[:20]}


@app.post("/dashboard/sessions")
async def create_session(request: LiveSessionRequest) -> dict[str, Any]:
    source = resolve_source(request)
    requested_mode = normalize_analysis_mode(request.analysis_mode)
    readiness = source.get("layout_readiness") or layout_readiness(
        Path(source["layout_path"]),
        source["camera_id"],
        source.get("role") or "zone",
        requested_mode,
    )
    effective_mode = readiness["effective_analysis_mode"]
    resolved_tracking = apply_auto_profile(
        store_id=source["store_id"],
        camera_id=source["camera_id"],
        role=source.get("role"),
        tracker_backend=request.tracker_backend,
        process_fps=request.process_fps,
        yolo_conf=request.yolo_conf,
        yolo_iou=request.yolo_iou,
        yolo_imgsz=request.yolo_imgsz,
        min_area=request.min_area,
        max_seconds=request.max_seconds,
    )
    initial_progress = session_progress("queued", "queued")
    session_id = uuid.uuid4().hex[:12]
    stop_event = threading.Event()
    SESSION_STOPS[session_id] = stop_event

    backend_session = post_json(
        "/sessions",
        {
            "session_id": session_id,
            "store_id": source["store_id"],
            "camera_id": source["camera_id"],
            "source_id": source.get("source_id"),
            "source_type": source.get("kind"),
            "source_label": request.session_label or source.get("label"),
            "video_path": source.get("video_path"),
            "status": "queued",
            "model_version": DEFAULT_MODEL_PATH.name,
            "calibration_version": Path(source["layout_path"]).name if source.get("layout_path") else None,
            "metadata": {
                "created_by": "dashboard",
                "session_label": request.session_label or source.get("session_label") or source.get("label"),
                "camera_role": source.get("role"),
                "analysis_mode_requested": requested_mode,
                "analysis_mode_effective": effective_mode,
                "layout_readiness": readiness,
                "max_seconds": resolved_tracking["max_seconds"],
                "process_fps": resolved_tracking["process_fps"],
                "replay_speed": request.replay_speed,
                "min_area": resolved_tracking["min_area"],
                "yolo_conf": resolved_tracking["yolo_conf"],
                "yolo_iou": resolved_tracking["yolo_iou"],
                "yolo_imgsz": resolved_tracking["yolo_imgsz"],
                "tracker_backend": resolved_tracking["tracker_backend"],
                "tracker_request": {
                    "tracker_backend": request.tracker_backend,
                    "process_fps": request.process_fps,
                    "min_area": request.min_area,
                    "yolo_conf": request.yolo_conf,
                    "yolo_iou": request.yolo_iou,
                    "yolo_imgsz": request.yolo_imgsz,
                },
                "tracking_profile": resolved_tracking["profile"],
                "auto_profile_applied": resolved_tracking["auto_profile_applied"],
            },
        },
    ).get("session")

    session = {
        "session_id": session_id,
        "backend_session": backend_session,
        "status": "queued",
        "stage": "queued",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "store_id": source["store_id"],
        "camera_id": source["camera_id"],
        "camera_role": source.get("role"),
        "analysis_mode": effective_mode,
        "requested_analysis_mode": requested_mode,
        "layout_readiness": readiness,
        "session_label": request.session_label or source.get("session_label") or source.get("label"),
        "source": source,
        "max_seconds": resolved_tracking["max_seconds"],
        "process_fps": resolved_tracking["process_fps"],
        "replay_speed": request.replay_speed,
        "min_area": resolved_tracking["min_area"],
        "yolo": {
            "conf": resolved_tracking["yolo_conf"],
            "iou": resolved_tracking["yolo_iou"],
            "imgsz": resolved_tracking["yolo_imgsz"],
            "model_path": str(DEFAULT_MODEL_PATH),
            "tracker_backend": resolved_tracking["tracker_backend"],
        },
        "tracker_request": {
            "tracker_backend": request.tracker_backend,
            "process_fps": request.process_fps,
            "min_area": request.min_area,
            "yolo_conf": request.yolo_conf,
            "yolo_iou": request.yolo_iou,
            "yolo_imgsz": request.yolo_imgsz,
        },
        "tracking_profile": resolved_tracking["profile"],
        "auto_profile_applied": resolved_tracking["auto_profile_applied"],
        "generated_events": 0,
        "replayed_events": 0,
        "inserted_events": 0,
        "duplicate_events": 0,
        "overlay_path": None,
        "overlay_status": "waiting",
        "overlay_frames": 0,
        "overlay_tracks": 0,
        "progress": initial_progress,
        "last_events": [],
        "error": None,
    }

    with SESSION_LOCK:
        SESSIONS[session_id] = session

    worker = threading.Thread(
        target=run_session_worker,
        args=(session_id, source, request, stop_event),
        daemon=True,
        name=f"dashboard-live-session-{session_id}",
    )
    worker.start()
    return {"session": get_session_or_404(session_id)}


@app.post("/dashboard/sessions/{session_id}/stop")
async def stop_session(session_id: str) -> dict[str, Any]:
    stop_event = SESSION_STOPS.get(session_id)
    if not stop_event:
        raise HTTPException(status_code=404, detail="session not found")
    stop_event.set()
    update_backend_session(session_id, status="stopping")
    return {"session": update_session(session_id, status="stopping", progress=session_progress("events", "stopping"))}


@app.get("/dashboard/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    return {"session": get_session_or_404(session_id)}


@app.get("/dashboard/sessions/{session_id}/overlays")
async def get_session_overlays(session_id: str, limit: int = 5000) -> dict[str, Any]:
    session = get_session_or_404(session_id)
    return overlay_payload_for_session(session, limit=max(1, min(int(limit), 20000)))


@app.get("/dashboard/sessions/{session_id}/result")
async def get_session_result(session_id: str) -> dict[str, Any]:
    session = get_session_or_404(session_id)
    snapshot = await fetch_store_snapshot(session["store_id"], session_id=session_id)
    overlay = overlay_payload_for_session(session, limit=1)
    return {
        "session": session,
        "summary": {
            "status": session.get("status"),
            "store_id": session.get("store_id"),
            "camera_id": session.get("camera_id"),
            "analysis_mode": session.get("analysis_mode"),
            "requested_analysis_mode": session.get("requested_analysis_mode"),
            "event_count": session.get("inserted_events") or session.get("generated_events") or 0,
            "overlay_available": overlay["available"],
            "result_scope": "current_session",
        },
        "snapshot": snapshot,
        "artifacts": {
            "events_path": session.get("events_path"),
            "overlay_path": session.get("overlay_path"),
        },
    }


@app.get("/dashboard/sessions/{session_id}/report")
async def get_session_report(session_id: str, format: str = "md") -> Response:
    session = get_session_or_404(session_id)
    snapshot = await fetch_store_snapshot(session["store_id"], session_id=session_id)
    overlay = overlay_payload_for_session(session, limit=1)
    artifacts = {
        "events_path": session.get("events_path"),
        "overlay_path": session.get("overlay_path"),
    }
    report = build_session_report(
        session,
        snapshot,
        overlay_available=overlay["available"],
        artifacts=artifacts,
    )

    requested = format.lower().strip()
    if requested == "json":
        filename = safe_download_name(session_id, "_report.json")
        return Response(
            json.dumps(report, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if requested == "csv":
        filename = safe_download_name(session_id, "_report.csv")
        return Response(
            render_session_report_csv(report),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    if requested not in {"md", "markdown"}:
        raise HTTPException(status_code=400, detail={"error": "unsupported_report_format", "supported": ["md", "csv", "json"]})

    filename = safe_download_name(session_id, "_report.md")
    return Response(
        render_session_report_markdown(report),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/dashboard/sessions/{session_id}/stream")
async def stream_session(session_id: str) -> StreamingResponse:
    async def event_generator():
        while True:
            session = get_session_or_404(session_id)
            try:
                snapshot = await fetch_store_snapshot(session["store_id"], session_id=session["session_id"])
            except (OSError, URLError, TimeoutError) as exc:
                snapshot = {
                    "store_id": session["store_id"],
                    "updated_at": now_iso(),
                    "metrics": {},
                    "funnel": {},
                    "heatmap": {},
                    "anomalies": [],
                    "recent_events": [],
                    "errors": {"dashboard": str(exc)},
                }
            yield f"data: {json.dumps({'session': session, 'snapshot': snapshot})}\n\n"
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/dashboard/stream")
async def stream_metrics(store_id: str = "STORE_BLR_002") -> StreamingResponse:
    async def event_generator():
        while True:
            try:
                payload = await fetch_store_snapshot(store_id)
            except (OSError, URLError, TimeoutError) as exc:
                payload = {
                    "store_id": store_id,
                    "api_base_url": API_BASE_URL,
                    "updated_at": now_iso(),
                    "metrics": {},
                    "funnel": {},
                    "heatmap": {},
                    "anomalies": [],
                    "recent_events": [],
                    "errors": {"dashboard": str(exc)},
                }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
