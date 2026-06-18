from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.analytics import compute_anomalies, compute_funnel, compute_heatmap, compute_metrics, load_layout
from app.database import StoreDatabase


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a local metrics summary from SQLite.")
    parser.add_argument("--store-id", default="STORE_BLR_002")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "store_intelligence.sqlite3")
    parser.add_argument("--layout", type=Path, default=PROJECT_ROOT / "contracts" / "store_layout.json")
    args = parser.parse_args()

    db = StoreDatabase(args.db)
    db.initialize()
    events = db.fetch_events(args.store_id)
    pos = db.fetch_pos_transactions(args.store_id)
    layout = load_layout(args.layout)

    summary = {
        "metrics": compute_metrics(events, pos, layout),
        "funnel": compute_funnel(events, pos),
        "heatmap": compute_heatmap(events, layout),
        "anomalies": compute_anomalies(events, pos, layout),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
