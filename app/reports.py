from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _event_total(session: dict[str, Any], snapshot: dict[str, Any]) -> int:
    metrics = snapshot.get("metrics") or {}
    event_counts = metrics.get("event_counts") or {}
    quality = snapshot.get("quality") or {}
    return int(
        session.get("inserted_events")
        or session.get("generated_events")
        or event_counts.get("total")
        or quality.get("total_events")
        or 0
    )


def build_session_report(
    session: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    overlay_available: bool = False,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a manager-friendly report payload for one isolated CCTV session."""

    metrics = snapshot.get("metrics") or {}
    quality = snapshot.get("quality") or {}
    queue = metrics.get("queue") or {}
    conversion = metrics.get("conversion") or {}
    pos = metrics.get("pos") or {}
    funnel = snapshot.get("funnel") or {}
    heatmap = snapshot.get("heatmap") or {}
    zones = list(heatmap.get("zones") or [])
    zones.sort(key=lambda zone: _safe_number(zone.get("normalized_score")), reverse=True)
    anomalies = snapshot.get("anomalies") or []
    source = session.get("source") or {}

    conversion_rate = conversion.get("display_rate_pct", metrics.get("conversion_rate_pct", 0))
    conversion_confidence = conversion.get("confidence") or ("CONFIRMED" if pos.get("transaction_count") else "ESTIMATED")

    return {
        "title": "Store Intelligence Session Report",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": "current_session_only",
        "session": {
            "session_id": session.get("session_id"),
            "label": session.get("session_label") or source.get("session_label") or source.get("label"),
            "status": session.get("status"),
            "store_id": session.get("store_id"),
            "camera_id": session.get("camera_id") or source.get("camera_id"),
            "camera_role": session.get("camera_role") or source.get("role"),
            "analysis_mode": session.get("analysis_mode"),
            "source_label": source.get("label") or session.get("source_label"),
            "created_at": session.get("created_at"),
            "completed_at": session.get("completed_at"),
        },
        "kpis": {
            "visitors": int(metrics.get("unique_visitors") or 0),
            "events": _event_total(session, snapshot),
            "queue_depth": int(queue.get("latest_depth") or metrics.get("queue_depth") or 0),
            "queue_max_depth": int(queue.get("max_depth") or 0),
            "queue_abandoned": int(queue.get("abandoned_visitor_count") or 0),
            "conversion_rate_pct": round(_safe_number(conversion_rate), 2),
            "conversion_confidence": conversion_confidence,
            "pos_transactions": int(pos.get("transaction_count") or 0),
            "pos_matched": int(pos.get("matched_transaction_count") or 0),
        },
        "quality": {
            "score": quality.get("quality_score"),
            "grade": quality.get("quality_grade"),
            "low_confidence_events": quality.get("low_confidence_events", 0),
            "avg_confidence": quality.get("avg_confidence", 0),
            "window": quality.get("window") or snapshot.get("window") or {},
        },
        "funnel": funnel.get("stages") or [],
        "top_zones": zones[:5],
        "alerts": anomalies[:5],
        "camera_health": snapshot.get("camera_health") or [],
        "overlay": {
            "available": bool(overlay_available),
            "frame_count": session.get("overlay_frames") or 0,
            "track_count": session.get("overlay_tracks") or 0,
        },
        "artifacts": artifacts or {},
        "notes": [
            "Metrics are scoped to this CCTV session only.",
            "Confirmed conversion requires POS data; otherwise conversion is estimated from billing behavior.",
            "Low-confidence signals are retained in quality metrics instead of being hidden.",
        ],
    }


def render_session_report_markdown(report: dict[str, Any]) -> str:
    session = report["session"]
    kpis = report["kpis"]
    quality = report["quality"]
    overlay = report["overlay"]

    lines = [
        "# Store Intelligence Session Report",
        "",
        f"- Session: {session.get('session_id') or '--'}",
        f"- Store: {session.get('store_id') or '--'}",
        f"- Camera: {session.get('camera_id') or '--'} ({session.get('camera_role') or 'unknown'})",
        f"- Status: {session.get('status') or '--'}",
        "- Scope: Current Session Only",
        f"- Generated at: {report.get('generated_at')}",
        "",
        "## KPI Summary",
        f"- Visitors: {kpis['visitors']}",
        f"- Events analyzed: {kpis['events']}",
        f"- Queue depth: {kpis['queue_depth']} current / {kpis['queue_max_depth']} max",
        f"- Queue abandoned: {kpis['queue_abandoned']}",
        f"- Conversion: {kpis['conversion_rate_pct']}% ({kpis['conversion_confidence']})",
        f"- POS matched: {kpis['pos_matched']} / {kpis['pos_transactions']}",
        "",
        "## Data Reliability",
        f"- Score: {quality.get('score', '--')}",
        f"- Grade: {quality.get('grade', '--')}",
        f"- Low-confidence events: {quality.get('low_confidence_events', 0)}",
        f"- Average event confidence: {quality.get('avg_confidence', 0)}",
        "",
        "## AI Overlay",
        f"- Overlay available: {'yes' if overlay.get('available') else 'no'}",
        f"- Overlay frames: {overlay.get('frame_count', 0)}",
        f"- Tracked person boxes: {overlay.get('track_count', 0)}",
        "",
        "## Funnel",
    ]

    funnel = report.get("funnel") or []
    if funnel:
        lines.extend([f"- {stage.get('stage')}: {stage.get('count', 0)}" for stage in funnel])
    else:
        lines.append("- No funnel events available for this session.")

    lines.extend(["", "## Top Zones"])
    zones = report.get("top_zones") or []
    if zones:
        for zone in zones:
            label = zone.get("label") or zone.get("zone_id") or "Zone"
            lines.append(f"- {label}: {zone.get('visits', zone.get('visit_count', 0))} visits")
    else:
        lines.append("- No zone activity available for this session.")

    lines.extend(["", "## Alerts"])
    alerts = report.get("alerts") or []
    if alerts:
        for alert in alerts:
            message = alert.get("message") or alert.get("anomaly_reason") or alert.get("type") or "Review needed"
            action = alert.get("suggested_action")
            lines.append(f"- {message}{f' Action: {action}' if action else ''}")
    else:
        lines.append("- No active alerts.")

    lines.extend(["", "## Notes"])
    lines.extend([f"- {note}" for note in report.get("notes", [])])
    return "\n".join(lines) + "\n"


def render_session_report_csv(report: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["section", "name", "value"])

    session = report["session"]
    for key in ["session_id", "label", "status", "store_id", "camera_id", "camera_role", "analysis_mode"]:
        writer.writerow(["session", key, session.get(key) or ""])

    for key, value in report["kpis"].items():
        writer.writerow(["kpi", key, value])

    for key, value in report["quality"].items():
        if isinstance(value, dict):
            value = "; ".join(f"{inner_key}={inner_value}" for inner_key, inner_value in value.items())
        writer.writerow(["quality", key, value])

    for stage in report.get("funnel") or []:
        writer.writerow(["funnel", stage.get("stage", ""), stage.get("count", 0)])

    for zone in report.get("top_zones") or []:
        writer.writerow(["top_zone", zone.get("zone_id") or zone.get("label") or "", zone.get("visits", zone.get("visit_count", 0))])

    for alert in report.get("alerts") or []:
        writer.writerow(["alert", alert.get("type") or "alert", alert.get("message") or alert.get("anomaly_reason") or ""])

    return output.getvalue()
