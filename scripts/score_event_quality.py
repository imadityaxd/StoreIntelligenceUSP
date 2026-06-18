from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.analytics import compute_metrics, load_layout
from app.models import StoreEvent


CANONICAL_CATALOGUE = {
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def is_low_confidence(event: dict[str, Any]) -> bool:
    metadata = event.get("metadata") or {}
    return isinstance(metadata, dict) and metadata.get("data_confidence_flag") == "LOW"


def is_inferred(event: dict[str, Any]) -> bool:
    metadata = event.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    source = str(metadata.get("source", ""))
    return source.startswith("v2_inspired")


def validate_schema(events: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    valid_count = 0
    for index, event in enumerate(events):
        try:
            StoreEvent.model_validate(event)
            valid_count += 1
        except Exception as exc:
            errors.append({"index": index, "event_id": event.get("event_id"), "error": str(exc)})
    return valid_count, errors


def score_fraction(value: float, weight: int) -> int:
    return max(0, min(weight, round(value * weight)))


def build_quality_report(
    events: list[dict[str, Any]],
    pos_transactions: list[dict[str, Any]],
    layout: dict[str, Any],
) -> dict[str, Any]:
    valid_count, schema_errors = validate_schema(events)
    total = len(events)
    counts = Counter(event.get("event_type", "UNKNOWN") for event in events)
    per_camera = defaultdict(Counter)
    for event in events:
        per_camera[event.get("camera_id", "UNKNOWN")][event.get("event_type", "UNKNOWN")] += 1

    configured_camera_ids = [
        camera["camera_id"]
        for camera in layout.get("cameras", [])
        if camera.get("camera_id")
    ]
    cameras_with_events = {event.get("camera_id") for event in events}
    covered_configured = [camera_id for camera_id in configured_camera_ids if camera_id in cameras_with_events]

    entry = counts.get("ENTRY", 0)
    exit_ = counts.get("EXIT", 0)
    entry_exit_balance = 1.0
    if max(entry, exit_) > 0:
        entry_exit_balance = 1.0 - abs(entry - exit_) / max(entry, exit_)

    present_catalogue = {event_type for event_type in CANONICAL_CATALOGUE if counts.get(event_type, 0) > 0}
    queue_join = counts.get("BILLING_QUEUE_JOIN", 0)
    queue_abandon = counts.get("BILLING_QUEUE_ABANDON", 0)
    queue_score_ratio = 0.0
    if queue_join > 0:
        queue_score_ratio = 0.7
        if queue_abandon <= queue_join:
            queue_score_ratio += 0.3

    low_conf_count = sum(1 for event in events if is_low_confidence(event))
    inferred_count = sum(1 for event in events if is_inferred(event))
    low_conf_rate = low_conf_count / total if total else 0.0
    inferred_rate = inferred_count / total if total else 0.0
    inference_score_ratio = max(0.0, 1.0 - (low_conf_rate * 1.5) - (inferred_rate * 1.0))

    metrics = compute_metrics(events, pos_transactions, layout)
    pos_match_rate = (
        metrics["pos"]["matched_transaction_count"] / metrics["pos"]["transaction_count"]
        if metrics["pos"]["transaction_count"]
        else 0.0
    )

    staff_visitors = {event["visitor_id"] for event in events if event.get("is_staff")}
    score_breakdown = {
        "schema_validity": score_fraction(valid_count / total if total else 0.0, 20),
        "configured_camera_coverage": score_fraction(
            len(covered_configured) / len(configured_camera_ids) if configured_camera_ids else 0.0,
            15,
        ),
        "entry_exit_balance": score_fraction(entry_exit_balance, 15),
        "event_catalogue_coverage": score_fraction(len(present_catalogue) / len(CANONICAL_CATALOGUE), 15),
        "queue_session_sanity": score_fraction(queue_score_ratio, 15),
        "pos_matching_signal": score_fraction(min(pos_match_rate * 5, 1.0), 10),
        "inference_risk_control": score_fraction(inference_score_ratio, 5),
        "staff_signal": 5 if staff_visitors else 0,
    }
    usp_quality_score = sum(score_breakdown.values())

    warnings: list[str] = []
    if schema_errors:
        warnings.append("Schema validation errors are present.")
    if "CAM_4" not in configured_camera_ids:
        warnings.append("CAM_4 is not configured in the active layout, so that feed is not scored.")
    if counts.get("REENTRY", 0) == 0:
        warnings.append("No REENTRY signal is present.")
    if pos_match_rate < 0.1:
        warnings.append("POS match rate is low; clock sync and billing-zone calibration need review.")
    if inferred_rate > 0.15:
        warnings.append("Inferred-event share is high; collect/review ground truth before final USP claims.")

    recommendations = [
        "Add a human review/annotation loop for false positives and missed visitors.",
        "Replace centroid association with ByteTrack or BoT-SORT for crowded/occluded scenes.",
        "Calibrate every camera from sampled frames and keep zone/layout versions.",
        "Track observed-only metrics separately from LOW-confidence inferred metrics.",
    ]

    return {
        "usp_quality_score": usp_quality_score,
        "score_breakdown": score_breakdown,
        "total_events": total,
        "event_counts": dict(sorted(counts.items())),
        "per_camera_event_counts": {
            camera_id: dict(sorted(camera_counts.items()))
            for camera_id, camera_counts in sorted(per_camera.items())
        },
        "configured_cameras": configured_camera_ids,
        "covered_configured_cameras": covered_configured,
        "schema": {
            "valid_rows": valid_count,
            "invalid_rows": len(schema_errors),
            "errors": schema_errors[:10],
        },
        "quality_rates": {
            "entry_exit_balance": round(entry_exit_balance, 4),
            "low_confidence_rate": round(low_conf_rate, 4),
            "inferred_event_rate": round(inferred_rate, 4),
            "pos_match_rate": round(pos_match_rate, 4),
        },
        "signals": {
            "staff_visitor_count": len(staff_visitors),
            "pos_matched_transaction_count": metrics["pos"]["matched_transaction_count"],
            "pos_transaction_count": metrics["pos"]["transaction_count"],
            "queue_join_count": queue_join,
            "queue_abandon_count": queue_abandon,
        },
        "warnings": warnings,
        "recommendations": recommendations,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# USP Event Quality Report",
        f"- USP quality score: {report['usp_quality_score']}/100",
        f"- Total events: {report['total_events']}",
        f"- Configured cameras covered: {len(report['covered_configured_cameras'])}/{len(report['configured_cameras'])}",
        "",
        "## Score Breakdown",
    ]
    for key, value in report["score_breakdown"].items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Quality Rates"])
    for key, value in report["quality_rates"].items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Event Counts"])
    for key, value in report["event_counts"].items():
        lines.append(f"- {key}: {value}")

    lines.extend(["", "## Warnings"])
    if report["warnings"]:
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.append("- None")

    lines.extend(["", "## Recommendations"])
    for recommendation in report["recommendations"]:
        lines.append(f"- {recommendation}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score final event quality for the USP build.")
    parser.add_argument("--events", type=Path, default=Path("data/events_phase8_submission.jsonl"))
    parser.add_argument("--pos", type=Path, default=Path("data/pos_transactions.json"))
    parser.add_argument("--layout", type=Path, default=Path("contracts/store_layout.json"))
    parser.add_argument("--report-json", type=Path, default=Path("data/reports/usp_quality_report.json"))
    parser.add_argument("--report-md", type=Path, default=Path("data/reports/usp_quality_report.md"))
    args = parser.parse_args()

    events = load_jsonl(args.events)
    pos_transactions = json.loads(args.pos.read_text(encoding="utf-8")) if args.pos.exists() else []
    layout = load_layout(args.layout)
    report = build_quality_report(events, pos_transactions, layout)

    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, args.report_md)

    print(json.dumps({
        "usp_quality_score": report["usp_quality_score"],
        "total_events": report["total_events"],
        "warnings": report["warnings"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
