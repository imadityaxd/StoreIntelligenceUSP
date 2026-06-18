from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import main as main_module
from app.analytics import load_layout
from app.database import StoreDatabase
from app.models import StoreEvent
from app.operations import (
    build_visitor_timeline,
    compute_camera_health,
    compute_event_quality,
    filter_events,
)
from app.registry import load_store_layouts


def load_sample_event_dicts() -> list[dict]:
    events = []
    path = PROJECT_ROOT / "contracts" / "sample_events.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            events.append(StoreEvent.model_validate_json(line).model_dump(mode="json"))
    return events


class BackendProductApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = load_layout(PROJECT_ROOT / "contracts" / "store_layout.json")
        self.events = load_sample_event_dicts()

    def test_registry_discovers_configured_store_cameras_and_zones(self) -> None:
        registry = load_store_layouts(
            [
                PROJECT_ROOT / "contracts" / "store_layout.json",
                PROJECT_ROOT / "contracts" / "store_layout_store2.json",
            ]
        )

        self.assertIn("STORE_BLR_002", registry)
        profile = registry["STORE_BLR_002"]["profile"]
        self.assertEqual(profile["camera_count"], 4)
        self.assertTrue(any(camera["camera_id"] == "CAM_5" and camera["role"] == "billing" for camera in profile["cameras"]))
        self.assertTrue(any(zone["zone_id"] == "BILLING_COUNTER" for zone in profile["zones"]))

    def test_camera_health_reports_configured_and_observed_cameras(self) -> None:
        health = compute_camera_health(self.events, self.layout)
        by_camera = {camera["camera_id"]: camera for camera in health}

        self.assertIn("CAM_5", by_camera)
        self.assertEqual(by_camera["CAM_5"]["role"], "billing")
        self.assertGreater(by_camera["CAM_5"]["event_count"], 0)
        self.assertIn(by_camera["CAM_2"]["status"], {"NO_EVENTS", "ONLINE", "STALE", "DEGRADED"})

    def test_event_quality_flags_low_confidence_payloads(self) -> None:
        events = [dict(event) for event in self.events]
        events[0]["confidence"] = 0.25
        events[1]["metadata"] = {**events[1]["metadata"], "data_confidence_flag": "LOW"}

        quality = compute_event_quality(events)

        self.assertEqual(quality["low_confidence_events"], 2)
        self.assertLess(quality["quality_score"], 100)
        self.assertIn("ENTRY", quality["event_type_counts"])

    def test_event_search_and_timeline_support_operator_drilldown(self) -> None:
        billing = filter_events(self.events, event_type="BILLING_QUEUE_JOIN", camera_id="CAM_5", sort="asc")
        timeline = build_visitor_timeline(self.events, "VIS_0001")

        self.assertTrue(billing)
        self.assertTrue(all(event["event_type"] == "BILLING_QUEUE_JOIN" for event in billing))
        self.assertEqual(timeline["visitor_id"], "VIS_0001")
        self.assertTrue(timeline["entered"])
        self.assertTrue(timeline["billing_queue_joined"])
        self.assertGreaterEqual(timeline["event_count"], 1)

    def test_overview_route_function_uses_temporary_database(self) -> None:
        original_database = main_module.database
        with tempfile.TemporaryDirectory() as tmpdir:
            db = StoreDatabase(Path(tmpdir) / "store.sqlite3")
            db.initialize()
            db.insert_events([StoreEvent.model_validate(event) for event in self.events])
            main_module.database = db
            try:
                overview = main_module.get_store_overview("STORE_BLR_002")
                stores = main_module.list_stores()
            finally:
                main_module.database = original_database

        self.assertEqual(overview["store_id"], "STORE_BLR_002")
        self.assertIn("metrics", overview)
        self.assertIn("camera_health", overview)
        self.assertIn("quality", overview)
        self.assertTrue(any(store["store_id"] == "STORE_BLR_002" for store in stores["stores"]))

    def test_session_scoped_events_do_not_mix_inside_same_store(self) -> None:
        session_a = "SESSION_A"
        session_b = "SESSION_B"
        events_a = []
        events_b = []
        for event in self.events:
            row_a = {**event, "event_id": str(uuid4()), "session_id": session_a}
            row_b = {
                **event,
                "event_id": str(uuid4()),
                "session_id": session_b,
                "visitor_id": f"{event['visitor_id']}_B",
            }
            events_a.append(StoreEvent.model_validate(row_a))
            events_b.append(StoreEvent.model_validate(row_b))

        with tempfile.TemporaryDirectory() as tmpdir:
            db = StoreDatabase(Path(tmpdir) / "session.sqlite3")
            db.initialize()
            db.create_session({"session_id": session_a, "store_id": "STORE_BLR_002", "status": "created"})
            db.create_session({"session_id": session_b, "store_id": "STORE_BLR_002", "status": "created"})
            db.insert_events(events_a)
            db.insert_events(events_b)

            self.assertEqual(db.count_events("STORE_BLR_002"), 20)
            self.assertEqual(db.count_events("STORE_BLR_002", session_id=session_a), 10)
            self.assertEqual(db.count_events("STORE_BLR_002", session_id=session_b), 10)
            self.assertTrue(all(row["session_id"] == session_a for row in db.fetch_events("STORE_BLR_002", session_id=session_a)))
            self.assertEqual(db.get_session(session_a)["event_count"], 10)

    def test_overview_route_can_be_filtered_by_session_id(self) -> None:
        original_database = main_module.database
        session_a = "SESSION_ROUTE_A"
        session_b = "SESSION_ROUTE_B"
        events = []
        for event in self.events:
            events.append(StoreEvent.model_validate({**event, "event_id": str(uuid4()), "session_id": session_a}))
            events.append(
                StoreEvent.model_validate(
                    {
                        **event,
                        "event_id": str(uuid4()),
                        "session_id": session_b,
                        "visitor_id": f"{event['visitor_id']}_B",
                    }
                )
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            db = StoreDatabase(Path(tmpdir) / "route-session.sqlite3")
            db.initialize()
            db.create_session({"session_id": session_a, "store_id": "STORE_BLR_002", "status": "created"})
            db.create_session({"session_id": session_b, "store_id": "STORE_BLR_002", "status": "created"})
            db.insert_events(events)
            main_module.database = db
            try:
                aggregate = main_module.get_store_overview("STORE_BLR_002")
                scoped = main_module.get_store_overview("STORE_BLR_002", session_id=session_a)
            finally:
                main_module.database = original_database

        self.assertEqual(aggregate["quality"]["total_events"], 20)
        self.assertEqual(scoped["quality"]["total_events"], 10)
        self.assertEqual(scoped["session"]["session_id"], session_a)


if __name__ == "__main__":
    unittest.main()
