from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.reports import build_session_report, render_session_report_csv, render_session_report_markdown
from dashboard import server as dashboard_server


def sample_session() -> dict:
    return {
        "session_id": "SESSION_REPORT",
        "session_label": "Morning CCTV check",
        "status": "complete",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_5",
        "camera_role": "billing",
        "analysis_mode": "billing",
        "inserted_events": 12,
        "overlay_frames": 40,
        "overlay_tracks": 4,
        "source": {"label": "CAM_5 billing", "camera_id": "CAM_5", "role": "billing"},
    }


def sample_snapshot() -> dict:
    return {
        "metrics": {
            "unique_visitors": 5,
            "event_counts": {"total": 12},
            "queue": {"latest_depth": 2, "max_depth": 4, "abandoned_visitor_count": 1},
            "conversion": {"display_rate_pct": 40.0, "confidence": "ESTIMATED"},
            "pos": {"transaction_count": 0, "matched_transaction_count": 0},
        },
        "quality": {
            "quality_score": 91.5,
            "quality_grade": "A",
            "low_confidence_events": 1,
            "avg_confidence": 0.84,
        },
        "funnel": {
            "stages": [
                {"stage": "entry", "count": 5},
                {"stage": "billing_queue", "count": 2},
            ]
        },
        "heatmap": {
            "zones": [
                {"zone_id": "BILLING_COUNTER", "label": "Billing Counter", "visits": 3, "normalized_score": 87},
                {"zone_id": "MARS_NY_BAE", "label": "Mars/NY Bae", "visits": 1, "normalized_score": 40},
            ]
        },
        "anomalies": [{"type": "QUEUE", "message": "Queue needs attention", "suggested_action": "Open support counter"}],
        "camera_health": [{"camera_id": "CAM_5", "status": "ONLINE"}],
    }


class Phase14SessionReportTests(unittest.TestCase):
    def test_report_payload_is_current_session_scoped(self) -> None:
        report = build_session_report(sample_session(), sample_snapshot(), overlay_available=True)

        self.assertEqual(report["scope"], "current_session_only")
        self.assertEqual(report["session"]["session_id"], "SESSION_REPORT")
        self.assertEqual(report["kpis"]["visitors"], 5)
        self.assertEqual(report["kpis"]["events"], 12)
        self.assertEqual(report["quality"]["grade"], "A")
        self.assertTrue(report["overlay"]["available"])
        self.assertEqual(report["top_zones"][0]["zone_id"], "BILLING_COUNTER")

    def test_report_markdown_and_csv_are_business_readable(self) -> None:
        report = build_session_report(sample_session(), sample_snapshot(), overlay_available=True)
        markdown = render_session_report_markdown(report)
        csv_text = render_session_report_csv(report)

        self.assertIn("# Store Intelligence Session Report", markdown)
        self.assertIn("Current Session Only", markdown)
        self.assertIn("## KPI Summary", markdown)
        self.assertIn("## Data Reliability", markdown)
        self.assertIn("Billing Counter", markdown)
        self.assertIn("section,name,value", csv_text)
        self.assertIn("kpi,visitors,5", csv_text)
        self.assertIn("quality,grade,A", csv_text)

    def test_dashboard_report_endpoint_exports_markdown_csv_and_json(self) -> None:
        original_sessions = dict(dashboard_server.SESSIONS)
        original_fetch_store_snapshot = dashboard_server.fetch_store_snapshot

        async def fake_fetch_store_snapshot(store_id: str, session_id: str | None = None) -> dict:
            self.assertEqual(store_id, "STORE_BLR_002")
            self.assertEqual(session_id, "SESSION_REPORT")
            return sample_snapshot()

        dashboard_server.fetch_store_snapshot = fake_fetch_store_snapshot
        dashboard_server.SESSIONS.clear()
        dashboard_server.SESSIONS["SESSION_REPORT"] = sample_session()
        try:
            md_response = asyncio.run(dashboard_server.get_session_report("SESSION_REPORT", format="md"))
            csv_response = asyncio.run(dashboard_server.get_session_report("SESSION_REPORT", format="csv"))
            json_response = asyncio.run(dashboard_server.get_session_report("SESSION_REPORT", format="json"))
        finally:
            dashboard_server.SESSIONS.clear()
            dashboard_server.SESSIONS.update(original_sessions)
            dashboard_server.fetch_store_snapshot = original_fetch_store_snapshot

        self.assertIn("SESSION_REPORT_report.md", md_response.headers["content-disposition"])
        self.assertIn("Store Intelligence Session Report", md_response.body.decode("utf-8"))
        self.assertIn("text/csv", csv_response.media_type)
        self.assertIn("kpi,queue_depth,2", csv_response.body.decode("utf-8"))
        payload = json.loads(json_response.body.decode("utf-8"))
        self.assertEqual(payload["session"]["session_id"], "SESSION_REPORT")
        self.assertEqual(payload["scope"], "current_session_only")

    def test_dashboard_markup_exposes_report_download_actions(self) -> None:
        markup = (PROJECT_ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="sessionReportActions"', markup)
        self.assertIn('id="downloadReportMd"', markup)
        self.assertIn('id="downloadReportCsv"', markup)
        self.assertIn('id="downloadReportJson"', markup)
        self.assertIn("updateReportLinks(session)", markup)
        self.assertIn("/dashboard/sessions/${encodeURIComponent(session.session_id)}/report?format=${format}", markup)


if __name__ == "__main__":
    unittest.main()
