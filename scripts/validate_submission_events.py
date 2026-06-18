# PROMPT BLOCK
# This script was designed with AI assistance (Claude Sonnet).
# Goal: Validate every line of the final submission JSONL against the
#       canonical StoreEvent Pydantic schema and print a pass/fail summary.
# AI suggestions accepted:
#   - Use Pydantic model_validate for strict schema checking
#   - Exit code 1 on any schema failure so CI can catch regressions
# AI suggestions rejected:
#   - Auto-repair invalid rows (too risky for submission integrity)

"""validate_submission_events.py

Reads a JSONL file (default: data/events_phase8_submission.jsonl) and
validates every line against app.models.StoreEvent.  Prints a summary
and exits with code 0 only if every row is valid.

Usage:
    python scripts/validate_submission_events.py
    python scripts/validate_submission_events.py --events data/events_phase8_submission.jsonl
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import StoreEvent


def main():
    parser = argparse.ArgumentParser(description="Validate submission JSONL against StoreEvent schema.")
    parser.add_argument(
        "--events",
        default="data/events_phase8_submission.jsonl",
        help="Path to the submission JSONL file.",
    )
    args = parser.parse_args()

    events_path = Path(args.events)
    if not events_path.exists():
        print(f"[ERROR] File not found: {events_path}")
        sys.exit(1)

    total = 0
    invalid = 0
    missing_timestamp = 0
    event_type_counter: Counter = Counter()
    errors = []

    with events_path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                invalid += 1
                errors.append(f"  Line {lineno}: JSON parse error - {exc}")
                continue

            # Check timestamp presence before Pydantic (gives clearer message)
            if not raw.get("timestamp"):
                missing_timestamp += 1

            try:
                event = StoreEvent.model_validate(raw)
                event_type_counter[event.event_type] += 1
            except Exception as exc:
                invalid += 1
                errors.append(f"  Line {lineno}: Schema error - {exc}")

    # -- Summary -------------------------------------------------------------
    print("=" * 60)
    print("SUBMISSION VALIDATION REPORT")
    print("=" * 60)
    print(f"File            : {events_path}")
    print(f"Total rows      : {total}")
    print(f"Invalid rows    : {invalid}")
    print(f"Missing ts      : {missing_timestamp}")
    print()
    print("Event type breakdown:")
    for etype, count in sorted(event_type_counter.items(), key=lambda x: -x[1]):
        print(f"  {etype:<35} {count:>5}")
    print()

    if errors:
        print("Errors (first 20):")
        for e in errors[:20]:
            print(e)
        print()

    # Low-confidence metadata summary (informational only, not a failure)
    low_conf_path = events_path
    low_conf_count = 0
    with low_conf_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                meta = raw.get("metadata") or {}
                if isinstance(meta, dict) and meta.get("data_confidence_flag") == "LOW":
                    low_conf_count += 1
            except Exception:
                pass
    print(f"Low-confidence events (metadata.data_confidence_flag=LOW): {low_conf_count}")
    print()

    # -- Pass / Fail ----------------------------------------------------------
    if invalid == 0 and total > 0:
        print("RESULT: PASS - all rows are schema-valid.")
        sys.exit(0)
    elif total == 0:
        print("RESULT: FAIL - file is empty.")
        sys.exit(1)
    else:
        print(f"RESULT: FAIL - {invalid} invalid row(s) found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
