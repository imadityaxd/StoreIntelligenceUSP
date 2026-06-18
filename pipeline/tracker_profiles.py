from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "contracts" / "tracker_profiles.json"

TRACKER_BACKENDS = {"bytetrack", "botsort", "centroid"}


def _as_float(value: Any, default: float, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _as_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _normalize_role(role: str | None) -> str:
    return (role or "zone").strip().lower().replace(" ", "_").replace("staff_area", "staff-area")


def normalize_profile_backend(value: Any, default: str = "bytetrack") -> str:
    normalized = str(value or default).strip().lower().replace("-", "").replace("_", "")
    aliases = {
        "byte": "bytetrack",
        "bytetrack": "bytetrack",
        "bot": "botsort",
        "botsort": "botsort",
        "centroid": "centroid",
        "simple": "centroid",
    }
    return aliases.get(normalized, default if default in TRACKER_BACKENDS else "bytetrack")


def load_tracker_profiles(path: Path | None = None) -> dict[str, Any]:
    profile_path = path or DEFAULT_PROFILE_PATH
    if not profile_path.exists():
        return {
            "version_id": "TRACKER_PROFILES_FALLBACK",
            "default_profile": {
                "tracker_backend": "bytetrack",
                "process_fps": 3.0,
                "yolo_conf": 0.4,
                "yolo_iou": 0.28,
                "yolo_imgsz": 960,
                "min_area": 1300,
                "max_seconds": 180,
            },
            "role_profiles": {},
            "camera_profiles": {},
        }
    return json.loads(profile_path.read_text(encoding="utf-8"))


def clean_tracker_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_name": str(profile.get("profile_name") or "auto"),
        "tracker_backend": normalize_profile_backend(profile.get("tracker_backend")),
        "process_fps": _as_float(profile.get("process_fps"), 3.0, 0.25, 12.0),
        "yolo_conf": _as_float(profile.get("yolo_conf"), 0.4, 0.05, 0.95),
        "yolo_iou": _as_float(profile.get("yolo_iou"), 0.28, 0.05, 0.95),
        "yolo_imgsz": _as_int(profile.get("yolo_imgsz"), 960, 320, 1920),
        "min_area": _as_int(profile.get("min_area"), 1300, 100, 100_000),
        "max_seconds": _as_float(profile.get("max_seconds"), 180.0, 3.0, 900.0),
    }


def profile_for_camera(
    *,
    store_id: str,
    camera_id: str,
    role: str | None,
    profile_path: Path | None = None,
) -> dict[str, Any]:
    payload = load_tracker_profiles(profile_path)
    merged: dict[str, Any] = {}
    merged.update(payload.get("default_profile") or {})
    normalized_role = _normalize_role(role)
    merged.update((payload.get("role_profiles") or {}).get(normalized_role) or {})
    merged.update(((payload.get("camera_profiles") or {}).get(store_id) or {}).get(camera_id) or {})
    cleaned = clean_tracker_profile(merged)
    cleaned.update(
        {
            "profile_version": payload.get("version_id") or "TRACKER_PROFILES_UNKNOWN",
            "store_id": store_id,
            "camera_id": camera_id,
            "camera_role": normalized_role,
        }
    )
    return cleaned


def apply_auto_profile(
    *,
    store_id: str,
    camera_id: str,
    role: str | None,
    tracker_backend: str | None,
    process_fps: float | None,
    yolo_conf: float | None,
    yolo_iou: float | None,
    yolo_imgsz: int | None,
    min_area: int | None,
    max_seconds: float | None,
    profile_path: Path | None = None,
) -> dict[str, Any]:
    profile = profile_for_camera(
        store_id=store_id,
        camera_id=camera_id,
        role=role,
        profile_path=profile_path,
    )
    auto_tracker = not tracker_backend or tracker_backend == "auto"
    resolved = {
        "profile": profile,
        "tracker_backend": profile["tracker_backend"] if auto_tracker else normalize_profile_backend(tracker_backend),
        "process_fps": profile["process_fps"] if auto_tracker or process_fps is None else _as_float(process_fps, profile["process_fps"], 0.25, 12.0),
        "yolo_conf": profile["yolo_conf"] if auto_tracker or yolo_conf is None else _as_float(yolo_conf, profile["yolo_conf"], 0.05, 0.95),
        "yolo_iou": profile["yolo_iou"] if auto_tracker or yolo_iou is None else _as_float(yolo_iou, profile["yolo_iou"], 0.05, 0.95),
        "yolo_imgsz": profile["yolo_imgsz"] if auto_tracker or yolo_imgsz is None else _as_int(yolo_imgsz, profile["yolo_imgsz"], 320, 1920),
        "min_area": profile["min_area"] if auto_tracker or min_area is None else _as_int(min_area, profile["min_area"], 100, 100_000),
        "max_seconds": profile["max_seconds"] if max_seconds is None else _as_float(max_seconds, profile["max_seconds"], 3.0, 900.0),
        "auto_profile_applied": auto_tracker,
    }
    return resolved
