# PROMPT BLOCK
# This script was designed with AI assistance (Claude Sonnet).
# Goal: Post-process raw pipeline JSONL into cleaner events suitable for
#       submission hardening. Handles REENTRY detection, billing queue
#       session state, and zone churn suppression.
# AI suggestions accepted:
#   - Keep canonical event_type enum; encode uncertainty in metadata fields
#     (data_confidence_flag, quality_reason) rather than creating *_LOW_CONF
#     event type variants.
#   - Use a two-pass approach for billing queue sessions.
# AI suggestions rejected:
#   - Auto-drop low-confidence events (loses recall signal).

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def get_ts(ev: dict[str, Any]) -> str:
    return str(ev.get("event_ts") or ev.get("timestamp") or "")


def _mark_low_confidence(ev: dict[str, Any], reason: str) -> None:
    """Mutate ev in-place: keep canonical event_type, flag metadata."""
    if not isinstance(ev.get("metadata"), dict):
        ev["metadata"] = {}
    ev["metadata"]["data_confidence_flag"] = "LOW"
    ev["metadata"]["quality_reason"] = reason
    ev["metadata"]["confidence_raw"] = float(ev.get("confidence", 0.5))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--infile", required=True)
    ap.add_argument("--outfile", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    in_path = Path(args.infile)
    out_path = Path(args.outfile)
    report_path = Path(args.report)

    events: list[dict[str, Any]] = []
    for line in in_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))

    events = [e for _, e in sorted(enumerate(events), key=lambda t: (get_ts(t[1]), t[0]))]

    # -- Pass 1: ENTRY -> REENTRY (visitor-based) -----------------------------
    seen_exit_by_cam_visitor: set[tuple[str, str]] = set()
    reentry_converted = 0

    for ev in events:
        cam = str(ev.get("camera_id", "UNKNOWN"))
        vid = str(ev.get("visitor_id", ""))
        et = str(ev.get("event_type", ""))

        if not vid:
            continue

        key = (cam, vid)
        if et == "EXIT":
            seen_exit_by_cam_visitor.add(key)
        elif et == "ENTRY" and key in seen_exit_by_cam_visitor:
            ev["event_type"] = "REENTRY"
            ev["postprocess_reason"] = "prior_exit_same_visitor_same_camera"
            ev["confidence"] = min(1.0, float(ev.get("confidence", 0.6)) + 0.15)
            reentry_converted += 1

    # -- Pass 2: reduce false billing abandons --------------------------------
    # Identify abandons that are followed by a later JOIN (visitor rejoined ->
    # the abandon was probably spurious noise).
    join_count_before: defaultdict[tuple[str, str], int] = defaultdict(int)
    later_join_exists: set[tuple[str, str]] = set()

    seen_future_join: set[tuple[str, str]] = set()
    for ev in reversed(events):
        cam = str(ev.get("camera_id", "UNKNOWN"))
        vid = str(ev.get("visitor_id", ""))
        et = str(ev.get("event_type", ""))
        if not vid:
            continue
        key = (cam, vid)
        if et == "BILLING_QUEUE_JOIN":
            seen_future_join.add(key)
        elif et == "BILLING_QUEUE_ABANDON" and key in seen_future_join:
            later_join_exists.add(key)

    abandons_downgraded = 0
    for ev in events:
        cam = str(ev.get("camera_id", "UNKNOWN"))
        vid = str(ev.get("visitor_id", ""))
        et = str(ev.get("event_type", ""))
        if not vid:
            continue
        key = (cam, vid)

        if et == "BILLING_QUEUE_JOIN":
            join_count_before[key] += 1
            continue

        if et == "BILLING_QUEUE_ABANDON":
            duplicate_join_noise = join_count_before[key] >= 2
            rejoins_later = key in later_join_exists
            if duplicate_join_noise or rejoins_later:
                # <- CHANGED: keep BILLING_QUEUE_ABANDON, mark LOW in metadata
                reason = "duplicate_queue_joins_or_rejoin_after_abandon"
                _mark_low_confidence(ev, reason)
                ev["confidence"] = min(float(ev.get("confidence", 0.5)), 0.45)
                ev["postprocess_reason"] = reason
                abandons_downgraded += 1

    # -- Pass 3: zone churn suppression ---------------------------------------
    inside_state: dict[tuple[str, str, str], bool] = {}
    zone_enter_downgraded = 0
    zone_exit_downgraded = 0

    for ev in events:
        et = str(ev.get("event_type", ""))
        if et not in ("ZONE_ENTER", "ZONE_EXIT"):
            continue

        cam = str(ev.get("camera_id", "UNKNOWN"))
        vid = str(ev.get("visitor_id", ""))
        zid = str(ev.get("zone_id", ""))

        if not vid or not zid:
            continue

        key = (cam, vid, zid)
        currently_inside = inside_state.get(key, False)

        if et == "ZONE_ENTER":
            if currently_inside:
                # <- CHANGED: keep ZONE_ENTER, mark LOW in metadata
                reason = "duplicate_zone_enter_without_exit"
                _mark_low_confidence(ev, reason)
                ev["confidence"] = min(float(ev.get("confidence", 0.5)), 0.40)
                ev["postprocess_reason"] = reason
                zone_enter_downgraded += 1
            else:
                inside_state[key] = True

        elif et == "ZONE_EXIT":
            if not currently_inside:
                # <- CHANGED: keep ZONE_EXIT, mark LOW in metadata
                reason = "zone_exit_without_prior_enter"
                _mark_low_confidence(ev, reason)
                ev["confidence"] = min(float(ev.get("confidence", 0.5)), 0.40)
                ev["postprocess_reason"] = reason
                zone_exit_downgraded += 1
            else:
                inside_state[key] = False


    # -- Pass 4: preserve upstream staff labels -------------------------------
    # Billing-only camera sessions are legitimate customer behavior in the
    # provided footage, so post-processing must not promote them to staff.
    # We trust the upstream detector/emitter's is_staff signal here.
    staff_visitor_ids: set[str] = set()
    staff_flagged_count = 0

   


    # -- Write output ---------------------------------------------------------
    # Move pipeline-internal audit fields into metadata so downstream
    # schema validation against StoreEvent (extra="forbid") passes cleanly.
    INTERNAL_FIELDS = {"postprocess_reason"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for ev in events:
            if not isinstance(ev.get("metadata"), dict):
                ev["metadata"] = {}
            for field in INTERNAL_FIELDS:
                if field in ev:
                    ev["metadata"][field] = ev.pop(field)
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    counts = Counter(ev.get("event_type", "UNKNOWN") for ev in events)
    low_conf_count = sum(
        1 for ev in events
        if isinstance(ev.get("metadata"), dict)
        and ev["metadata"].get("data_confidence_flag") == "LOW"
    )

    per_camera: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for ev in events:
        cam = str(ev.get("camera_id", "UNKNOWN"))
        et = str(ev.get("event_type", "UNKNOWN"))
        per_camera[cam][et] += 1

    report = {
        "input_file": str(in_path),
        "output_file": str(out_path),
        "total_events": len(events),
        "reentry_converted": reentry_converted,
        "staff_visitors_flagged": len(staff_visitor_ids),
        "staff_events_flagged": staff_flagged_count,
        "abandons_low_confidence": abandons_downgraded,
        "zone_enter_low_confidence": zone_enter_downgraded,
        "zone_exit_low_confidence": zone_exit_downgraded,
        "total_low_confidence_events": low_conf_count,
        "event_counts": dict(sorted(counts.items())),
        "per_camera_event_counts": {
            cam: dict(sorted(v.items())) for cam, v in sorted(per_camera.items())
        },
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
