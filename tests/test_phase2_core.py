# PROMPT: Generate tests for a store-intelligence API core that validates idempotent event ingestion, staff exclusion, funnel session deduplication, POS matching within five minutes, heatmap confidence, and empty-store behavior.
# CHANGES MADE: Replaced generic fixtures with the actual Phase 1 sample_events.jsonl contract and kept tests on the sqlite/analytics core so they run before FastAPI is installed.

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.analytics import compute_funnel, compute_heatmap, compute_metrics
from app.database import StoreDatabase
from app.models import StoreEvent


def load_sample_events() -> list[StoreEvent]:
    events = []
    path = PROJECT_ROOT / "contracts" / "sample_events.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            events.append(StoreEvent.model_validate_json(line))
    return events


class Phase2CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = StoreDatabase(Path(self.tmp.name) / "test.sqlite3")
        self.db.initialize()
        self.events = load_sample_events()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_event_ingestion_is_idempotent(self) -> None:
        first = self.db.insert_events(self.events)
        second = self.db.insert_events(self.events)

        self.assertEqual(first.inserted, 10)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.duplicates, 10)
        self.assertEqual(self.db.count_events("STORE_BLR_002"), 10)

    def test_metrics_exclude_staff(self) -> None:
        self.db.insert_events(self.events)
        rows = self.db.fetch_events("STORE_BLR_002")
        metrics = compute_metrics(rows, [])

        self.assertEqual(metrics["unique_visitors"], 2)
        self.assertEqual(metrics["purchase_count"], 0)
        self.assertEqual(metrics["avg_dwell_ms_per_zone"]["TOP_WALL_SKINCARE"], 30000)

    def test_pos_transaction_matches_billing_visitor_within_five_minutes(self) -> None:
        self.db.insert_events(self.events)
        self.db.upsert_pos_transactions(
            [
                {
                    "transaction_id": "TXN_SAMPLE_1",
                    "store_id": "STORE_BLR_002",
                    "timestamp": "2026-04-10T07:14:00Z",
                    "basket_value_inr": 999.0,
                    "metadata": {},
                }
            ]
        )

        rows = self.db.fetch_events("STORE_BLR_002")
        pos = self.db.fetch_pos_transactions("STORE_BLR_002")
        metrics = compute_metrics(rows, pos)
        funnel = compute_funnel(rows, pos)

        self.assertEqual(metrics["purchase_count"], 1)
        self.assertEqual(metrics["converted_visitor_ids"], ["VIS_0001"])
        self.assertEqual(funnel["stages"][0]["count"], 2)
        self.assertEqual(funnel["stages"][3]["count"], 1)

    def test_heatmap_marks_low_confidence_for_small_sample(self) -> None:
        self.db.insert_events(self.events)
        layout = json.loads((PROJECT_ROOT / "contracts" / "store_layout.json").read_text(encoding="utf-8-sig"))
        heatmap = compute_heatmap(self.db.fetch_events("STORE_BLR_002"), layout)

        self.assertEqual(heatmap["data_confidence"], "LOW")
        self.assertTrue(len(heatmap["zones"]) > 0)

    def test_empty_store_returns_zero_metrics(self) -> None:
        metrics = compute_metrics([], [])
        funnel = compute_funnel([], [])

        self.assertEqual(metrics["unique_visitors"], 0)
        self.assertEqual(metrics["conversion_rate"], 0.0)
        self.assertEqual(funnel["stages"][0]["count"], 0)


if __name__ == "__main__":
    unittest.main()

