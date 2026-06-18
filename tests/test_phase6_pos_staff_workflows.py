from __future__ import annotations

import asyncio
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import main as main_module
from app.analytics import compute_metrics
from app.database import StoreDatabase
from app.models import StoreEvent
from dashboard import server as dashboard_server
from scripts.import_pos import parse_mapped_csv


def make_event(
    visitor_id: str,
    event_type: str,
    timestamp: str,
    *,
    session_id: str | None = None,
    zone_id: str | None = None,
    is_staff: bool = False,
) -> StoreEvent:
    return StoreEvent.model_validate(
        {
            "event_id": str(uuid4()),
            "session_id": session_id,
            "store_id": "STORE_BLR_002",
            "camera_id": "CAM_5" if zone_id == "BILLING_COUNTER" else "CAM_1",
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": timestamp,
            "zone_id": zone_id,
            "dwell_ms": 0,
            "is_staff": is_staff,
            "confidence": 0.9,
            "metadata": {
                "queue_depth": 1 if event_type == "BILLING_QUEUE_JOIN" else None,
                "session_seq": 1,
            },
        }
    )


class Phase6PosStaffWorkflowTests(unittest.TestCase):
    def test_generic_pos_mapping_groups_rows_and_normalizes_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "mapped_pos.csv"
            rows = [
                {"bill": "BILL_1", "sold_at": "2026-04-10 12:00:00", "store": "STORE_BLR_002", "net": "100.25"},
                {"bill": "BILL_1", "sold_at": "2026-04-10 12:00:00", "store": "STORE_BLR_002", "net": "200.75"},
                {"bill": "BILL_2", "sold_at": "2026-04-10 12:05:00", "store": "STORE_BLR_002", "net": "50"},
            ]
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)

            transactions = parse_mapped_csv(
                csv_path,
                transaction_id_column="bill",
                amount_column="net",
                timestamp_column="sold_at",
                store_id_column="store",
                timezone_offset="+05:30",
            )

        self.assertEqual(len(transactions), 2)
        self.assertEqual(transactions[0]["transaction_id"], "BILL_1")
        self.assertEqual(transactions[0]["basket_value_inr"], 301.0)
        self.assertEqual(transactions[0]["timestamp"], "2026-04-10T06:30:00Z")
        self.assertEqual(transactions[0]["metadata"]["source"], "mapped_pos_csv")

    def test_metrics_show_estimated_conversion_until_pos_is_imported(self) -> None:
        events = [
            make_event("VIS_1", "ENTRY", "2026-04-10T07:00:00Z").model_dump(mode="json"),
            make_event("VIS_1", "BILLING_QUEUE_JOIN", "2026-04-10T07:04:00Z", zone_id="BILLING_COUNTER").model_dump(mode="json"),
            make_event("VIS_2", "ENTRY", "2026-04-10T07:08:00Z").model_dump(mode="json"),
        ]

        without_pos = compute_metrics(events, [])
        with_pos = compute_metrics(
            events,
            [
                {
                    "transaction_id": "INV_1",
                    "store_id": "STORE_BLR_002",
                    "timestamp": "2026-04-10T07:08:30Z",
                    "basket_value_inr": 499.0,
                    "metadata": {},
                }
            ],
        )

        self.assertEqual(without_pos["conversion"]["confidence"], "ESTIMATED")
        self.assertEqual(without_pos["conversion"]["display_count"], 1)
        self.assertEqual(without_pos["conversion"]["display_rate_pct"], 50.0)
        self.assertEqual(without_pos["conversion_rate_pct"], 0.0)
        self.assertEqual(with_pos["conversion"]["confidence"], "CONFIRMED")
        self.assertEqual(with_pos["conversion"]["display_count"], 1)
        self.assertEqual(with_pos["pos"]["matched_transaction_count"], 1)

    def test_staff_corrections_are_persisted_and_applied_to_fetches(self) -> None:
        original_database = main_module.database
        with tempfile.TemporaryDirectory() as tmp:
            db = StoreDatabase(Path(tmp) / "phase6.sqlite3")
            db.initialize()
            db.insert_events(
                [
                    make_event("VIS_1", "ENTRY", "2026-04-10T07:00:00Z", session_id="SESSION_1"),
                    make_event("VIS_1", "ZONE_ENTER", "2026-04-10T07:01:00Z", session_id="SESSION_1", zone_id="FOH"),
                    make_event("VIS_2", "ENTRY", "2026-04-10T07:02:00Z", session_id="SESSION_1"),
                ]
            )
            db.upsert_visitor_correction(
                {
                    "store_id": "STORE_BLR_002",
                    "session_id": "SESSION_1",
                    "visitor_id": "VIS_1",
                    "is_staff": True,
                    "reason": "operator_dashboard_review",
                }
            )

            main_module.database = db
            try:
                corrected = main_module.fetch_corrected_events("STORE_BLR_002", session_id="SESSION_1")
            finally:
                main_module.database = original_database

        vis_1 = [event for event in corrected if event["visitor_id"] == "VIS_1"]
        vis_2 = [event for event in corrected if event["visitor_id"] == "VIS_2"]
        self.assertTrue(vis_1)
        self.assertTrue(all(event["is_staff"] for event in vis_1))
        self.assertTrue(all(event["metadata"]["staff_correction_applied"] for event in vis_1))
        self.assertTrue(all(not event["is_staff"] for event in vis_2))

    def test_dashboard_snapshot_and_pos_staff_routes_proxy_backend_contracts(self) -> None:
        calls: list[tuple[str, object | None]] = []
        original_fetch_json = dashboard_server.fetch_json
        original_post_json = dashboard_server.post_json

        def fake_fetch_json(path: str) -> dict:
            calls.append((path, None))
            if "/pos/matches" in path:
                return {"store_id": "STORE_BLR_002", "matches": [], "conversion": {}, "pos": {}}
            return {"store_id": "STORE_BLR_002", "metrics": {}, "recent_events": []}

        def fake_post_json(path: str, payload: object) -> dict:
            calls.append((path, payload))
            return {"ok": True}

        dashboard_server.fetch_json = fake_fetch_json
        dashboard_server.post_json = fake_post_json
        try:
            snapshot = asyncio.run(dashboard_server.get_dashboard_store_snapshot("STORE_BLR_002", session_id="SESSION_1"))
            matches = asyncio.run(dashboard_server.dashboard_pos_matches("STORE_BLR_002", session_id="SESSION_1"))
            correction = asyncio.run(
                dashboard_server.dashboard_staff_correction(
                    "STORE_BLR_002",
                    "VIS_1",
                    {"session_id": "SESSION_1", "is_staff": True, "reason": "test"},
                )
            )
        finally:
            dashboard_server.fetch_json = original_fetch_json
            dashboard_server.post_json = original_post_json

        self.assertEqual(snapshot["store_id"], "STORE_BLR_002")
        self.assertEqual(matches["store_id"], "STORE_BLR_002")
        self.assertTrue(correction["ok"])
        self.assertIn(("/stores/STORE_BLR_002/overview?session_id=SESSION_1", None), calls)
        self.assertIn(("/stores/STORE_BLR_002/pos/matches?session_id=SESSION_1", None), calls)
        self.assertTrue(any(path == "/stores/STORE_BLR_002/visitors/VIS_1/staff-correction" for path, _ in calls))


if __name__ == "__main__":
    unittest.main()
