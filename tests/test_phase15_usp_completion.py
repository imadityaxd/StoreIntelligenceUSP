from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dashboard import server as dashboard_server


def backend_session(session_id: str = "SESSION_PERSISTED") -> dict:
    return {
        "session_id": session_id,
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_5",
        "source_id": None,
        "source_type": "file",
        "source_label": "Persisted billing run",
        "video_path": None,
        "status": "complete",
        "created_at": "2026-06-17T10:00:00Z",
        "started_at": "2026-06-17T10:00:02Z",
        "completed_at": "2026-06-17T10:02:00Z",
        "analysis_start": None,
        "analysis_end": None,
        "model_version": "yolov8s.pt",
        "calibration_version": "store_layout.json",
        "event_count": 17,
        "error": None,
        "metadata": {
            "created_by": "dashboard",
            "session_label": "Persisted billing run",
            "camera_role": "billing",
            "analysis_mode_requested": "billing",
            "analysis_mode_effective": "billing",
            "tracker_backend": "botsort",
            "tracking_profile": {"tracker_backend": "botsort", "profile_name": "billing_counter"},
        },
    }


def overview_payload(session_id: str = "SESSION_PERSISTED") -> dict:
    return {
        "store_id": "STORE_BLR_002",
        "session_id": session_id,
        "metrics": {
            "unique_visitors": 6,
            "event_counts": {"total": 17},
            "queue": {"latest_depth": 1, "max_depth": 3, "abandoned_visitor_count": 1},
            "conversion": {"display_rate_pct": 33.3, "confidence": "ESTIMATED"},
            "pos": {"transaction_count": 0, "matched_transaction_count": 0},
        },
        "quality": {"quality_score": 89.0, "quality_grade": "B", "low_confidence_events": 2, "avg_confidence": 0.78},
        "funnel": {"stages": [{"stage": "billing_queue", "count": 3}]},
        "heatmap": {"zones": [{"zone_id": "BILLING_COUNTER", "label": "Billing Counter", "visits": 3, "normalized_score": 90}]},
        "anomalies": [],
        "camera_health": [{"camera_id": "CAM_5", "status": "ONLINE"}],
        "recent_events": [],
    }


class Phase15UspCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_sessions = dict(dashboard_server.SESSIONS)
        self.original_fetch_json = dashboard_server.fetch_json
        dashboard_server.SESSIONS.clear()

    def tearDown(self) -> None:
        dashboard_server.SESSIONS.clear()
        dashboard_server.SESSIONS.update(self.original_sessions)
        dashboard_server.fetch_json = self.original_fetch_json

    def test_dashboard_session_can_be_recovered_from_backend_storage(self) -> None:
        session = dashboard_server.dashboard_session_from_backend(backend_session())

        self.assertIsNotNone(session)
        self.assertEqual(session["session_id"], "SESSION_PERSISTED")
        self.assertEqual(session["status"], "complete")
        self.assertEqual(session["store_id"], "STORE_BLR_002")
        self.assertEqual(session["camera_id"], "CAM_5")
        self.assertEqual(session["camera_role"], "billing")
        self.assertEqual(session["inserted_events"], 17)
        self.assertEqual(session["yolo"]["tracker_backend"], "botsort")
        self.assertTrue(session["recovered"])

    def test_session_history_merges_persistent_backend_sessions(self) -> None:
        def fake_fetch_json(path: str) -> dict:
            self.assertEqual(path, "/sessions?limit=50")
            return {"sessions": [backend_session()]}

        dashboard_server.fetch_json = fake_fetch_json

        payload = asyncio.run(dashboard_server.list_sessions())

        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["session_id"], "SESSION_PERSISTED")
        self.assertEqual(payload["sessions"][0]["inserted_events"], 17)
        self.assertEqual(payload["sessions"][0]["stage"], "recovered")

    def test_persisted_session_can_export_report_after_dashboard_restart(self) -> None:
        def fake_fetch_json(path: str) -> dict:
            if path == "/sessions/SESSION_PERSISTED":
                return {"session": backend_session()}
            if path == "/stores/STORE_BLR_002/overview?session_id=SESSION_PERSISTED":
                return overview_payload()
            raise AssertionError(f"Unexpected fetch path: {path}")

        dashboard_server.fetch_json = fake_fetch_json

        response = asyncio.run(dashboard_server.get_session_report("SESSION_PERSISTED", format="json"))
        report = json.loads(response.body.decode("utf-8"))

        self.assertEqual(report["scope"], "current_session_only")
        self.assertEqual(report["session"]["session_id"], "SESSION_PERSISTED")
        self.assertEqual(report["kpis"]["events"], 17)
        self.assertEqual(report["kpis"]["visitors"], 6)
        self.assertEqual(report["quality"]["grade"], "B")

    def test_dashboard_keeps_session_recovery_hooks_without_visible_history_panel(self) -> None:
        markup = (PROJECT_ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("<summary>Session History", markup)
        self.assertIn('id="sessionHistoryList"', markup)
        self.assertIn('id="refreshSessionsBtn"', markup)
        self.assertIn("function loadSessions()", markup)
        self.assertIn("function openHistoricalSession(sessionId)", markup)
        self.assertIn('data-open-session-id="${id}"', markup)
        self.assertIn("loadSessions()]", markup)


if __name__ == "__main__":
    unittest.main()
