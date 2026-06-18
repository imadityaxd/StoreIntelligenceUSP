from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def load_time_offsets(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def clip_start_utc(camera_id: str, config: dict[str, Any]) -> datetime:
    cameras = config["cameras"]
    camera = cameras[camera_id]
    reference_local = datetime.fromisoformat(camera["reference_local"])
    reference_second = float(camera.get("reference_second", config.get("default_reference_second", 10.0)))
    return reference_local.astimezone(timezone.utc) - timedelta(seconds=reference_second)


def frame_timestamp_iso(camera_id: str, frame_index: int, fps: float, config: dict[str, Any]) -> str:
    start = clip_start_utc(camera_id, config)
    seconds = frame_index / fps if fps > 0 else 0.0
    return to_utc_iso(start + timedelta(seconds=seconds))
