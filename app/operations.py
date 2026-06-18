from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from app.analytics import compute_anomalies, compute_funnel, compute_heatmap, compute_metrics, fmt_ts, parse_ts


def is_low_confidence_event(event: dict[str, Any]) -> bool:
    metadata = event.get("metadata") or {}
    if isinstance(metadata, dict) and metadata.get("data_confidence_flag") == "LOW":
        return True
    if str(event.get("event_type", "")).endswith("_LOW_CONF"):
        return True
    return float(event.get("confidence", 1.0)) < 0.5


def _event_time_range(events: list[dict[str, Any]]) -> dict[str, str | None]:
    if not events:
        return {"start": None, "end": None}
    timestamps = [parse_ts(event["timestamp"]) for event in events]
    return {"start": fmt_ts(min(timestamps)), "end": fmt_ts(max(timestamps))}


def compute_event_quality(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(events)
    event_type_counts = Counter(event["event_type"] for event in events)
    camera_counts = Counter(event["camera_id"] for event in events)
    low_confidence = [event for event in events if is_low_confidence_event(event)]
    inferred = [
        event
        for event in events
        if str((event.get("metadata") or {}).get("source", "")).startswith("v2_inspired")
        or (event.get("metadata") or {}).get("quality_reason")
    ]
    zone_events_missing_zone = [
        event
        for event in events
        if event["event_type"] in {"ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"}
        and not event.get("zone_id")
    ]
    avg_confidence = round(mean([float(event["confidence"]) for event in events]), 3) if events else 0.0

    low_ratio = len(low_confidence) / total if total else 0.0
    inferred_ratio = len(inferred) / total if total else 0.0
    missing_zone_ratio = len(zone_events_missing_zone) / total if total else 0.0
    quality_score = round(max(0.0, min(100.0, 100 - low_ratio * 35 - inferred_ratio * 10 - missing_zone_ratio * 30)), 1)

    if total == 0:
        grade = "NO_DATA"
    elif quality_score >= 90:
        grade = "A"
    elif quality_score >= 80:
        grade = "B"
    elif quality_score >= 70:
        grade = "C"
    else:
        grade = "NEEDS_ATTENTION"

    return {
        "window": _event_time_range(events),
        "total_events": total,
        "customer_events": sum(1 for event in events if not event["is_staff"]),
        "staff_events": sum(1 for event in events if event["is_staff"]),
        "low_confidence_events": len(low_confidence),
        "inferred_or_adjusted_events": len(inferred),
        "zone_events_missing_zone": len(zone_events_missing_zone),
        "avg_confidence": avg_confidence,
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "camera_event_counts": dict(sorted(camera_counts.items())),
        "quality_score": quality_score,
        "quality_grade": grade,
    }


def compute_camera_health(
    events: list[dict[str, Any]],
    layout: dict[str, Any],
    *,
    reference_time: datetime | None = None,
    stale_after_seconds: int = 600,
) -> list[dict[str, Any]]:
    events_by_camera: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_camera[event["camera_id"]].append(event)

    if reference_time is None:
        reference_time = max((parse_ts(event["timestamp"]) for event in events), default=datetime.now(timezone.utc))
    reference_time = reference_time.astimezone(timezone.utc)

    configured = {
        camera.get("camera_id"): camera
        for camera in layout.get("cameras", [])
        if camera.get("camera_id")
    }
    observed_camera_ids = set(events_by_camera)
    camera_ids = sorted(set(configured) | observed_camera_ids)

    health: list[dict[str, Any]] = []
    for camera_id in camera_ids:
        camera = configured.get(camera_id, {})
        rows = sorted(events_by_camera.get(camera_id, []), key=lambda event: event["timestamp"])
        event_counts = Counter(event["event_type"] for event in rows)
        low_count = sum(1 for event in rows if is_low_confidence_event(event))
        latest_event_ts = parse_ts(rows[-1]["timestamp"]) if rows else None
        lag_seconds = int((reference_time - latest_event_ts).total_seconds()) if latest_event_ts else None
        low_ratio = low_count / len(rows) if rows else 0.0

        if not rows:
            status = "NO_EVENTS"
            reason = "No events observed in the selected window."
        elif lag_seconds is not None and lag_seconds > stale_after_seconds:
            status = "STALE"
            reason = f"No event observed for {lag_seconds} seconds in the selected window."
        elif low_ratio >= 0.4:
            status = "DEGRADED"
            reason = f"{low_ratio:.0%} of events are low confidence."
        else:
            status = "ONLINE"
            reason = "Camera is producing usable events."

        health.append(
            {
                "camera_id": camera_id,
                "role": camera.get("role", "unknown"),
                "description": camera.get("description"),
                "status": status,
                "reason": reason,
                "event_count": len(rows),
                "customer_event_count": sum(1 for event in rows if not event["is_staff"]),
                "staff_event_count": sum(1 for event in rows if event["is_staff"]),
                "low_confidence_event_count": low_count,
                "low_confidence_rate": round(low_ratio, 4),
                "latest_event_timestamp": fmt_ts(latest_event_ts) if latest_event_ts else None,
                "lag_seconds": lag_seconds,
                "event_type_counts": dict(sorted(event_counts.items())),
                "zone_ids": sorted(camera.get("zones_normalized", {}).keys()),
                "configured": camera_id in configured,
            }
        )

    return health


def filter_events(
    events: list[dict[str, Any]],
    *,
    event_type: str | None = None,
    camera_id: str | None = None,
    visitor_id: str | None = None,
    zone_id: str | None = None,
    is_staff: bool | None = None,
    low_confidence: bool | None = None,
    limit: int = 100,
    sort: str = "desc",
) -> list[dict[str, Any]]:
    rows = list(events)
    if event_type:
        rows = [event for event in rows if event["event_type"] == event_type]
    if camera_id:
        rows = [event for event in rows if event["camera_id"] == camera_id]
    if visitor_id:
        rows = [event for event in rows if event["visitor_id"] == visitor_id]
    if zone_id:
        rows = [event for event in rows if event.get("zone_id") == zone_id]
    if is_staff is not None:
        rows = [event for event in rows if event["is_staff"] is is_staff]
    if low_confidence is not None:
        rows = [event for event in rows if is_low_confidence_event(event) is low_confidence]

    reverse = sort.lower() != "asc"
    rows.sort(key=lambda event: (event["timestamp"], event["event_id"]), reverse=reverse)
    safe_limit = max(1, min(int(limit), 500))
    return rows[:safe_limit]


def build_visitor_timeline(events: list[dict[str, Any]], visitor_id: str) -> dict[str, Any]:
    rows = sorted(
        [event for event in events if event["visitor_id"] == visitor_id],
        key=lambda event: (event["timestamp"], event["event_id"]),
    )
    zones_seen = sorted({event["zone_id"] for event in rows if event.get("zone_id")})
    cameras_seen = sorted({event["camera_id"] for event in rows})
    total_dwell_ms = sum(int(event["dwell_ms"]) for event in rows if event["event_type"] == "ZONE_DWELL")

    return {
        "visitor_id": visitor_id,
        "event_count": len(rows),
        "window": _event_time_range(rows),
        "cameras_seen": cameras_seen,
        "zones_seen": zones_seen,
        "total_dwell_ms": total_dwell_ms,
        "total_dwell_seconds": round(total_dwell_ms / 1000, 2),
        "entered": any(event["event_type"] == "ENTRY" for event in rows),
        "exited": any(event["event_type"] == "EXIT" for event in rows),
        "reentry_count": sum(1 for event in rows if event["event_type"] == "REENTRY"),
        "billing_queue_joined": any(event["event_type"] == "BILLING_QUEUE_JOIN" for event in rows),
        "billing_queue_abandoned": any(event["event_type"] == "BILLING_QUEUE_ABANDON" for event in rows),
        "events": rows,
    }


def compute_store_overview(
    events: list[dict[str, Any]],
    pos_transactions: list[dict[str, Any]],
    layout: dict[str, Any],
    *,
    recent_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "window": _event_time_range(events),
        "metrics": compute_metrics(events, pos_transactions, layout),
        "funnel": compute_funnel(events, pos_transactions),
        "heatmap": compute_heatmap(events, layout),
        "anomalies": compute_anomalies(events, pos_transactions, layout),
        "camera_health": compute_camera_health(events, layout),
        "quality": compute_event_quality(events),
        "recent_events": recent_events if recent_events is not None else filter_events(events, limit=20),
    }
