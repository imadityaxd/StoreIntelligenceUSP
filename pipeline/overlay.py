from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TextIO

from pipeline.person_validation import validate_physical_person_track
from pipeline.timebase import frame_timestamp_iso
from pipeline.tracker import Track
from pipeline.zones import zones_for_point


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(value)))


def _normalize_bbox(bbox: list[int], frame_width: int, frame_height: int) -> list[float]:
    if frame_width <= 0 or frame_height <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x1, y1, x2, y2 = bbox
    return [
        round(x1 / frame_width, 6),
        round(y1 / frame_height, 6),
        round(x2 / frame_width, 6),
        round(y2 / frame_height, 6),
    ]


def track_to_overlay(
    track: Track,
    camera_config: dict[str, Any],
    frame_width: int,
    frame_height: int,
    visitor_id_for_track: Callable[[int], str] | None = None,
    staff_for_track: Callable[[int, str | None], bool] | None = None,
    validation_for_track: Callable[[int], Any | None] | None = None,
    min_confirmed_hits: int = 3,
) -> dict[str, Any] | None:
    x1 = _clamp(track.detection.x, 0, frame_width)
    y1 = _clamp(track.detection.y, 0, frame_height)
    x2 = _clamp(track.detection.x + track.detection.w, 0, frame_width)
    y2 = _clamp(track.detection.y + track.detection.h, 0, frame_height)
    if x2 <= x1 or y2 <= y1:
        return None

    zones = zones_for_point(camera_config, track.footpoint, frame_width, frame_height)
    zone_id = zones[0] if zones else None
    visitor_id = visitor_id_for_track(track.track_id) if visitor_id_for_track else f"P{track.track_id:03d}"
    is_staff = bool(staff_for_track(track.track_id, zone_id)) if staff_for_track else False
    validation = validation_for_track(track.track_id) if validation_for_track else None
    if validation is None:
        validation = validate_physical_person_track(
            track,
            frame_width,
            frame_height,
            min_confirmed_hits=min_confirmed_hits,
        )
    countable = True if validation is None else bool(getattr(validation, "countable", True))
    ignored_reason = None if validation is None else getattr(validation, "ignored_reason", None)
    physical_person_score = None if validation is None else getattr(validation, "physical_person_score", None)
    role = "suspect" if not countable else "staff" if is_staff else "person"
    label_parts = [visitor_id, role]
    if zone_id:
        label_parts.append(zone_id)

    bbox = [x1, y1, x2, y2]
    stale_seconds = 0.0
    if track.last_observed_frame is not None:
        # The caller supplies frame cadence through build_overlay_frame; seconds
        # are attached there where fps is available.
        stale_seconds = float(max(0, track.missed))
    return {
        "track_id": track.track_id,
        "visitor_id": visitor_id,
        "label": " / ".join(label_parts),
        "bbox": bbox,
        "bbox_normalized": _normalize_bbox(bbox, frame_width, frame_height),
        "confidence": round(float(track.detection.confidence), 3),
        "is_staff": is_staff,
        "person_type": role,
        "countable": countable,
        "physical_person_score": None if physical_person_score is None else round(float(physical_person_score), 3),
        "ignored_reason": ignored_reason,
        "zone_id": zone_id,
        "missed": track.missed,
        "track_status": "predicted" if track.missed > 0 else "tracked",
        "stale_samples": track.missed,
        "stale_seconds": stale_seconds,
        "hits": track.hits,
    }


def build_overlay_frame(
    *,
    session_id: str | None,
    camera_id: str,
    camera_config: dict[str, Any],
    time_config: dict[str, Any],
    frame_index: int,
    fps: float,
    frame_width: int,
    frame_height: int,
    tracks: list[Track],
    visitor_id_for_track: Callable[[int], str] | None = None,
    staff_for_track: Callable[[int, str | None], bool] | None = None,
    validation_for_track: Callable[[int], Any | None] | None = None,
    max_missed: int = 8,
    min_confirmed_hits: int = 3,
) -> dict[str, Any]:
    visible_tracks = [
        track
        for track in tracks
        if track.missed <= max_missed and track.detection.w > 0 and track.detection.h > 0
    ]
    overlay_tracks: list[dict[str, Any]] = []
    for track in visible_tracks:
        row = track_to_overlay(
            track,
            camera_config,
            frame_width,
            frame_height,
            visitor_id_for_track=visitor_id_for_track,
            staff_for_track=staff_for_track,
            validation_for_track=validation_for_track,
            min_confirmed_hits=min_confirmed_hits,
        )
        if row is None:
            continue
        if fps > 0:
            observed_frame = track.last_observed_frame if track.last_observed_frame is not None else track.last_frame
            row["stale_seconds"] = round(max(0, frame_index - observed_frame) / fps, 3)
        overlay_tracks.append(row)

    video_time_seconds = round(frame_index / fps, 3) if fps > 0 else 0.0
    return {
        "session_id": session_id,
        "camera_id": camera_id,
        "timestamp": frame_timestamp_iso(camera_id, frame_index, fps, time_config),
        "frame_index": frame_index,
        "video_time_seconds": video_time_seconds,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "tracks": overlay_tracks,
    }


def write_overlay_frame(handle: TextIO, frame: dict[str, Any]) -> None:
    handle.write(json.dumps(frame, separators=(",", ":")) + "\n")
    handle.flush()


def read_overlay_jsonl(path: Path, limit: int = 5000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    if limit > 0 and len(rows) > limit:
        return rows[-limit:]
    return rows
