from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import StoreDatabase
from app.models import StoreEvent


def read_events(path: Path) -> list[StoreEvent]:
    events: list[StoreEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                events.append(StoreEvent.model_validate_json(raw))
            except ValidationError as exc:
                raise ValueError(f"invalid event on line {line_number}: {exc}") from exc
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Load sample JSONL events into SQLite.")
    parser.add_argument("--events", type=Path, default=PROJECT_ROOT / "contracts" / "sample_events.jsonl")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "store_intelligence.sqlite3")
    args = parser.parse_args()

    db = StoreDatabase(args.db)
    db.initialize()
    result = db.insert_events(read_events(args.events))
    print(
        f"Loaded events: accepted={result.accepted}, inserted={result.inserted}, duplicates={result.duplicates}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
