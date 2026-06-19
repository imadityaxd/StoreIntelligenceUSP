# PROMPT: Create focused tests for POS/business-metric hardening in a retail CCTV intelligence API: invoice aggregation, five-minute POS matching boundaries, confidence flags, queue anomaly signals, and funnel math.
# CHANGES MADE: Used the project StoreEvent contract and Python unittest instead of pytest so the tests run on a clean Windows venv with no extra test dependencies.

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.analytics import compute_anomalies, compute_funnel, compute_heatmap, compute_metrics
from app.models import StoreEvent
from scripts.import_pos import parse_purplle_csv as parse_actual_purplle_csv


def make_event(
    visitor_id: str,
    event_type: str,
    timestamp: str,
    *,
    zone_id: str | None = None,
    queue_depth: int | None = None,
    is_staff: bool = False,
    confidence: float = 0.9,
    session_seq: int = 1,
) -> dict:
    payload = {
        "event_id": str(uuid4()),
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_4" if zone_id == "BILLING_COUNTER" else "CAM_1",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": 30000 if event_type == "ZONE_DWELL" else 0,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": "billing" if zone_id == "BILLING_COUNTER" else None,
            "session_seq": session_seq,
        },
    }
    return StoreEvent.model_validate(payload).model_dump(mode="json")


class Phase3BusinessMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = json.loads((PROJECT_ROOT / "contracts" / "store_layout.json").read_text(encoding="utf-8-sig"))

    def test_pos_import_groups_item_rows_by_invoice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "pos.csv"
            rows = [
                {
                    "order_id": "ORDER_1",
                    "invoice_number": "INV_1",
                    "order_date": "10-04-2026",
                    "order_time": "16:55:36",
                    "store_id": "STORE_BLR_002",
                    "store_name": "Brigade_Bangalore",
                    "total_amount": "100.25",
                    "NMV": "100.25",
                },
                {
                    "order_id": "ORDER_1",
                    "invoice_number": "INV_1",
                    "order_date": "10-04-2026",
                    "order_time": "16:55:36",
                    "store_id": "STORE_BLR_002",
                    "store_name": "Brigade_Bangalore",
                    "total_amount": "200.75",
                    "NMV": "200.75",
                },
            ]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            transactions = parse_actual_purplle_csv(csv_path)

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["transaction_id"], "INV_1")
        self.assertEqual(transactions[0]["basket_value_inr"], 301.0)
        self.assertEqual(transactions[0]["timestamp"], "2026-04-10T11:25:36Z")

    def test_metrics_distinguish_matched_and_unmatched_pos(self) -> None:
        events = [
            make_event("VIS_1", "ENTRY", "2026-04-10T07:00:00Z", session_seq=1),
            make_event(
                "VIS_1",
                "BILLING_QUEUE_JOIN",
                "2026-04-10T07:10:00Z",
                zone_id="BILLING_COUNTER",
                queue_depth=3,
                session_seq=2,
            ),
            make_event("VIS_2", "ENTRY", "2026-04-10T07:20:00Z", session_seq=1),
        ]
        pos = [
            {
                "transaction_id": "MATCHED",
                "store_id": "STORE_BLR_002",
                "timestamp": "2026-04-10T07:14:59Z",
                "basket_value_inr": 500.0,
                "metadata": {},
            },
            {
                "transaction_id": "UNMATCHED_TOO_LATE",
                "store_id": "STORE_BLR_002",
                "timestamp": "2026-04-10T07:15:01Z",
                "basket_value_inr": 700.0,
                "metadata": {},
            },
        ]

        metrics = compute_metrics(events, pos, self.layout)

        self.assertEqual(metrics["unique_visitors"], 2)
        self.assertEqual(metrics["purchase_count"], 1)
        self.assertEqual(metrics["conversion_rate_pct"], 50.0)
        self.assertEqual(metrics["pos"]["transaction_count"], 2)
        self.assertEqual(metrics["pos"]["matched_sales_inr"], 500.0)
        self.assertEqual(metrics["pos"]["unmatched_transaction_count"], 1)

    def test_funnel_uses_sessions_not_raw_events(self) -> None:
        events = [
            make_event("VIS_1", "ENTRY", "2026-04-10T07:00:00Z", session_seq=1),
            make_event("VIS_1", "ZONE_ENTER", "2026-04-10T07:01:00Z", zone_id="FOH", session_seq=2),
            make_event("VIS_1", "ZONE_DWELL", "2026-04-10T07:01:30Z", zone_id="FOH", session_seq=3),
            make_event("VIS_1", "ZONE_EXIT", "2026-04-10T07:02:10Z", zone_id="FOH", session_seq=4),
            make_event(
                "VIS_1",
                "BILLING_QUEUE_JOIN",
                "2026-04-10T07:03:00Z",
                zone_id="BILLING_COUNTER",
                queue_depth=1,
                session_seq=5,
            ),
        ]
        pos = [
            {
                "transaction_id": "TXN_1",
                "store_id": "STORE_BLR_002",
                "timestamp": "2026-04-10T07:04:00Z",
                "basket_value_inr": 100.0,
                "metadata": {},
            }
        ]

        funnel = compute_funnel(events, pos)

        self.assertEqual([stage["count"] for stage in funnel["stages"]], [1, 1, 1, 1])
        self.assertEqual(funnel["overall_conversion_pct"], 100.0)

    def test_heatmap_confidence_turns_high_after_twenty_sessions(self) -> None:
        events = [
            make_event(f"VIS_{idx}", "ENTRY", f"2026-04-10T07:{idx:02d}:00Z", session_seq=1)
            for idx in range(20)
        ]

        heatmap = compute_heatmap(events, self.layout)

        self.assertEqual(heatmap["data_confidence"], "HIGH")

    def test_queue_spike_anomaly_uses_latest_queue_depth(self) -> None:
        events = [
            make_event("VIS_1", "ENTRY", "2026-04-10T07:00:00Z", session_seq=1),
            make_event(
                "VIS_1",
                "BILLING_QUEUE_JOIN",
                "2026-04-10T07:01:00Z",
                zone_id="BILLING_COUNTER",
                queue_depth=8,
                session_seq=2,
            ),
        ]

        anomalies = compute_anomalies(events, [], self.layout)

        self.assertTrue(any(item["type"] == "BILLING_QUEUE_SPIKE" for item in anomalies))
        self.assertTrue(any(item["type"] == "LOW_DATA_CONFIDENCE" for item in anomalies))

    def test_zone_only_session_does_not_emit_conversion_warning(self) -> None:
        events = [
            make_event(
                f"VIS_{idx}",
                "ZONE_ENTER",
                f"2026-04-10T07:{idx:02d}:00Z",
                zone_id="ZONE_BACK_LEFT",
                session_seq=1,
            )
            for idx in range(6)
        ]

        anomalies = compute_anomalies(events, [], self.layout)

        self.assertNotIn("CONVERSION_DROP", {item["type"] for item in anomalies})
        self.assertNotIn("LOW_BASELINE_CONFIDENCE", {item["type"] for item in anomalies})

    def test_inactive_zones_are_scoped_to_observed_camera_and_collapsed(self) -> None:
        events = [
            make_event(
                "VIS_1",
                "ZONE_ENTER",
                "2026-04-10T07:00:00Z",
                zone_id="ZONE_BACK_LEFT",
                session_seq=1,
            )
        ]

        anomalies = compute_anomalies(events, [], self.layout)
        dead_zone_rows = [item for item in anomalies if item["type"] == "DEAD_ZONES_SUMMARY"]

        self.assertEqual(len(dead_zone_rows), 1)
        self.assertNotIn("ZONE_BACK_RIGHT", dead_zone_rows[0]["zone_ids"])
        self.assertNotIn("ZONE_FRONT_RIGHT", dead_zone_rows[0]["zone_ids"])


if __name__ == "__main__":
    unittest.main()
