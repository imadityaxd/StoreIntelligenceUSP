from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.models import StoreEvent


def validate_jsonl(path: Path) -> int:
    seen_event_ids: set[str] = set()
    valid_count = 0
    errors: list[str] = []

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                event = StoreEvent.model_validate_json(raw)
            except ValidationError as exc:
                errors.append(f"line {line_number}: {exc.errors()}")
                continue

            event_id = str(event.event_id)
            if event_id in seen_event_ids:
                errors.append(f"line {line_number}: duplicate event_id {event_id}")
                continue

            seen_event_ids.add(event_id)
            valid_count += 1

    if errors:
        print("Phase 1 validation failed")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"Phase 1 validation passed: {valid_count} events")
    return 0


def validate_layout(path: Path) -> int:
    layout = json.loads(path.read_text(encoding="utf-8"))
    zone_ids = {zone["zone_id"] for zone in layout["zones"]}
    missing: list[str] = []

    for camera_id, camera in layout["cameras"].items():
        for zone_id, polygon in camera.get("zones_normalized", {}).items():
            if zone_id not in zone_ids:
                missing.append(f"{camera_id} references unknown zone {zone_id}")
            if len(polygon) < 3:
                missing.append(f"{camera_id}/{zone_id} polygon has fewer than 3 points")
            for point in polygon:
                if len(point) != 2 or not all(0.0 <= float(value) <= 1.0 for value in point):
                    missing.append(f"{camera_id}/{zone_id} has non-normalized point {point}")

    if missing:
        print("Layout validation failed")
        for error in missing:
            print(f"- {error}")
        return 1

    print(f"Layout validation passed: {len(zone_ids)} zones, {len(layout['cameras'])} cameras")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 1 contracts.")
    parser.add_argument("--events", type=Path, default=Path("contracts/sample_events.jsonl"))
    parser.add_argument("--layout", type=Path, default=Path("contracts/store_layout.json"))
    args = parser.parse_args()

    layout_status = validate_layout(args.layout)
    event_status = validate_jsonl(args.events)
    return layout_status or event_status


if __name__ == "__main__":
    raise SystemExit(main())
