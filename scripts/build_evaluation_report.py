from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


inp = Path("data/events_phase8_submission.jsonl")
out_json = Path("data/reports/final_evaluation_report.json")
out_md = Path("data/reports/final_evaluation_report.md")


def is_low_confidence(event: dict) -> bool:
    metadata = event.get("metadata") or {}
    return isinstance(metadata, dict) and metadata.get("data_confidence_flag") == "LOW"


events = [json.loads(line) for line in inp.read_text(encoding="utf-8").splitlines() if line.strip()]
counts = Counter(event.get("event_type", "UNKNOWN") for event in events)
per_camera = defaultdict(Counter)
for event in events:
    per_camera[event.get("camera_id", "UNKNOWN")][event.get("event_type", "UNKNOWN")] += 1

joins = counts.get("BILLING_QUEUE_JOIN", 0)
abandons_total = counts.get("BILLING_QUEUE_ABANDON", 0)
abandons_low_conf = sum(
    1
    for event in events
    if event.get("event_type") == "BILLING_QUEUE_ABANDON" and is_low_confidence(event)
)
abandons_strict = abandons_total - abandons_low_conf
low_conf_total = sum(1 for event in events if is_low_confidence(event))

report = {
    "input_file": str(inp),
    "total_events": len(events),
    "event_counts": dict(sorted(counts.items())),
    "total_low_confidence_events": low_conf_total,
    "per_camera_event_counts": {
        camera: dict(sorted(values.items())) for camera, values in sorted(per_camera.items())
    },
    "kpis": {
        "billing_join_count": joins,
        "billing_abandon_total": abandons_total,
        "billing_abandon_strict": abandons_strict,
        "billing_abandon_low_confidence": abandons_low_conf,
        "billing_abandon_rate_strict": (abandons_strict / joins) if joins else None,
        "entry_exit_gap": counts.get("ENTRY", 0) - counts.get("EXIT", 0),
        "reentry_count": counts.get("REENTRY", 0),
    },
    "notes": [
        "Low-confidence events retain canonical event types and are flagged in metadata.",
        "LOW-confidence enrichment is used only when weak CCTV/POS signals need audit-visible coverage.",
        "REENTRY is reported only when observed or transparently inferred from an exit/entry registry.",
        "CAM_4 currently emits zero events and remains a documented detection limitation.",
    ],
}

out_json.parent.mkdir(parents=True, exist_ok=True)
out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

md = [
    "# Final Evaluation Report",
    f"- Total events: {len(events)}",
    f"- Total low-confidence events: {low_conf_total}",
    "",
    "## KPI Snapshot",
    f"- Billing joins: {joins}",
    f"- Billing abandons (total): {abandons_total}",
    f"- Billing abandons (strict): {abandons_strict}",
    f"- Billing abandons (low confidence): {abandons_low_conf}",
    f"- Billing abandon rate (strict): {report['kpis']['billing_abandon_rate_strict']}",
    f"- Entry-Exit gap: {report['kpis']['entry_exit_gap']}",
    f"- Reentry count: {report['kpis']['reentry_count']}",
    "",
    "## Per Camera Counts",
]
for camera, values in report["per_camera_event_counts"].items():
    md.append(f"### {camera}")
    for event_type, count in values.items():
        md.append(f"- {event_type}: {count}")
    md.append("")

out_md.write_text("\n".join(md), encoding="utf-8")
print(f"Wrote: {out_json}")
print(f"Wrote: {out_md}")
