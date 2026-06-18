from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import StoreDatabase
from app.models import StoreEvent
from dashboard import server as dashboard_server
from dashboard.server import LiveSessionRequest, known_sources, resolve_source
from pydantic import ValidationError


def load_sample_events() -> list[StoreEvent]:
    events = []
    path = PROJECT_ROOT / "contracts" / "sample_events.jsonl"
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            events.append(StoreEvent.model_validate_json(line))
    return events


class LiveDashboardTests(unittest.TestCase):
    def test_dashboard_first_screen_stays_business_facing(self) -> None:
        markup = (PROJECT_ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn("CCTV Analysis", markup)
        self.assertIn("Analyze one CCTV run", markup)
        self.assertIn("Saved CCTV source", markup)
        self.assertIn("Saved Clip", markup)
        self.assertIn("Upload Video", markup)
        self.assertIn("Live CCTV", markup)
        self.assertIn("Live CCTV stream", markup)
        self.assertIn('id="liveRtspUrl"', markup)
        self.assertIn("Upload CCTV video", markup)
        self.assertIn("Start Analysis", markup)
        self.assertIn("Analyzing...", markup)
        self.assertIn("ready-stop", markup)
        self.assertIn("Run Result", markup)
        self.assertIn("No active session", markup)
        self.assertIn("System checking", markup)
        self.assertIn("Data reliability pending", markup)
        self.assertIn("Start monitoring to build the funnel.", markup)
        self.assertIn("Start monitoring to populate zone activity.", markup)
        self.assertIn("Start monitoring to see live events.", markup)
        self.assertIn("No active alerts", markup)
        self.assertIn('id="aiOverlayCanvas"', markup)
        self.assertIn('id="showAiOverlay"', markup)
        self.assertIn('id="showZoneOverlay"', markup)
        self.assertIn('id="overlayChip"', markup)
        self.assertIn('id="guidedUploadFile"', markup)
        self.assertIn('id="guidedStoreId"', markup)
        self.assertIn('id="guidedCameraRole"', markup)
        self.assertIn('id="guidedAnalysisMode"', markup)
        self.assertIn('id="sessionProgressFill"', markup)
        self.assertIn('id="sessionProgressSteps"', markup)
        self.assertIn('id="sessionResultStatus"', markup)
        self.assertIn('id="sessionModeLabel"', markup)
        self.assertIn('id="sessionResultNote"', markup)
        self.assertIn("Live Intelligence", markup)
        self.assertIn('id="intelligenceHeadline"', markup)
        self.assertIn('id="intelligenceScope"', markup)
        self.assertIn('id="movementInsight"', markup)
        self.assertIn('id="zoneInsight"', markup)
        self.assertIn('id="queueInsight"', markup)
        self.assertIn('id="conversionInsight"', markup)
        self.assertIn("Start analysis to turn CCTV into valid person movement", markup)
        self.assertIn("Joined billing", markup)
        self.assertIn("Left billing queue", markup)
        self.assertIn("valid people", markup)
        self.assertIn("Analysis window ended at", markup)
        self.assertIn("Waiting for detection at", markup)
        self.assertIn('id="trackerBackend"', markup)
        self.assertIn('<option value="auto" selected>Auto profile</option>', markup)
        self.assertIn('<option value="bytetrack">ByteTrack</option>', markup)
        self.assertIn('<option value="botsort">BoT-SORT</option>', markup)
        self.assertIn('tracker_backend: $("trackerBackend").value', markup)
        self.assertIn('id="funnelInsight"', markup)
        self.assertIn('id="heatmapInsightSummary"', markup)
        self.assertNotIn("AI boxes", markup)
        self.assertIn('id="conversionSub"', markup)
        self.assertNotIn("Analyze New Video", markup)
        self.assertNotIn("Live Monitoring", markup)
        self.assertNotIn("Admin Setup", markup)
        self.assertNotIn("ML/Ops", markup)
        self.assertNotIn("Zone Calibration", markup)
        self.assertNotIn("<summary>System Health", markup)
        self.assertNotIn("<summary>Event Search</summary>", markup)
        self.assertNotIn("<summary>POS & Staff Review</summary>", markup)
        self.assertNotIn("<summary>Visitor Timeline</summary>", markup)
        self.assertNotIn("Camera role", markup)
        self.assertNotIn("Store setup", markup)
        self.assertNotIn("Analysis scope", markup)
        self.assertNotIn("RTSP URL", markup)
        self.assertNotIn("YOLO conf", markup)
        self.assertNotIn('id="generatedEvents"', markup)
        self.assertNotIn('id="replayedEvents"', markup)
        self.assertNotIn('id="insertedEvents"', markup)
        self.assertNotIn('id="duplicateEvents"', markup)
        self.assertNotIn("api_base_url", markup)
        self.assertNotIn("camera checks", markup)
        self.assertNotIn('details class="panel" open', markup)

    def test_recent_events_are_limited_and_returned_chronologically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = StoreDatabase(Path(tmp) / "events.sqlite3")
            db.initialize()
            db.insert_events(load_sample_events())

            rows = db.fetch_recent_events("STORE_BLR_002", limit=3)

        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["store_id"] == "STORE_BLR_002" for row in rows))
        self.assertEqual(
            [row["timestamp"] for row in rows],
            sorted(row["timestamp"] for row in rows),
        )

    def test_dashboard_discovers_known_cctv_sources(self) -> None:
        sources = known_sources()
        camera_roles = {(source["store_id"], source["camera_id"], source["role"]) for source in sources}

        self.assertIn(("STORE_BLR_002", "CAM_5", "billing"), camera_roles)
        self.assertTrue(any(source.get("media_url") for source in sources))
        billing = next(source for source in sources if source["store_id"] == "STORE_BLR_002" and source["camera_id"] == "CAM_5")
        self.assertEqual(billing["tracking_profile"]["tracker_backend"], "botsort")
        self.assertEqual(billing["tracking_profile"]["profile_name"], "store1_billing_counter")

    def test_dashboard_known_sources_do_not_repeat_saved_camera_labels(self) -> None:
        sources = known_sources()
        saved_sources = [source for source in sources if not source.get("created_at")]
        labels = [
            (source["store_id"], source["camera_id"], source["role"], source["label"])
            for source in saved_sources
        ]

        self.assertEqual(len(labels), len(set(labels)))

    def test_live_session_request_validates_tracker_backend(self) -> None:
        self.assertEqual(LiveSessionRequest().tracker_backend, "auto")
        self.assertEqual(LiveSessionRequest(tracker_backend="auto").tracker_backend, "auto")
        self.assertEqual(LiveSessionRequest(tracker_backend="bytetrack").tracker_backend, "bytetrack")
        self.assertEqual(LiveSessionRequest(tracker_backend="botsort").tracker_backend, "botsort")
        self.assertEqual(LiveSessionRequest(tracker_backend="centroid").tracker_backend, "centroid")
        with self.assertRaises(ValidationError):
            LiveSessionRequest(tracker_backend="mog2")

    def test_dashboard_snapshot_prefers_backend_overview(self) -> None:
        calls: list[str] = []
        original_fetch_json = dashboard_server.fetch_json

        def fake_fetch_json(path: str) -> dict:
            calls.append(path)
            return {
                "store_id": "STORE_BLR_002",
                "metrics": {"event_counts": {"total": 3}},
                "funnel": {},
                "heatmap": {},
                "anomalies": [],
                "camera_health": [{"camera_id": "CAM_1", "status": "ONLINE"}],
                "quality": {"quality_score": 96},
                "recent_events": [],
            }

        dashboard_server.fetch_json = fake_fetch_json
        try:
            snapshot = asyncio.run(dashboard_server.fetch_store_snapshot("STORE_BLR_002"))
        finally:
            dashboard_server.fetch_json = original_fetch_json

        self.assertEqual(calls, ["/stores/STORE_BLR_002/overview"])
        self.assertEqual(snapshot["quality"]["quality_score"], 96)
        self.assertEqual(snapshot["camera_health"][0]["status"], "ONLINE")

    def test_dashboard_event_search_proxies_to_backend_filters(self) -> None:
        calls: list[str] = []
        original_fetch_json = dashboard_server.fetch_json

        def fake_fetch_json(path: str) -> dict:
            calls.append(path)
            return {"store_id": "STORE_BLR_002", "count": 0, "events": []}

        dashboard_server.fetch_json = fake_fetch_json
        try:
            result = asyncio.run(
                dashboard_server.search_store_events(
                    "STORE_BLR_002",
                    event_type="ENTRY",
                    camera_id="CAM_3",
                    low_confidence=False,
                    limit=12,
                )
            )
        finally:
            dashboard_server.fetch_json = original_fetch_json

        self.assertEqual(result["count"], 0)
        self.assertIn("/stores/STORE_BLR_002/events/search?", calls[0])
        self.assertIn("event_type=ENTRY", calls[0])
        self.assertIn("camera_id=CAM_3", calls[0])
        self.assertIn("low_confidence=False", calls[0])
        self.assertIn("limit=12", calls[0])

    def test_saved_source_keeps_configured_store_context(self) -> None:
        source = next(item for item in known_sources() if item["store_id"] == "STORE_BLR_002")
        resolved = resolve_source(LiveSessionRequest(source_id=source["source_id"]))

        self.assertEqual(resolved["store_id"], "STORE_BLR_002")

    def test_uploaded_source_registry_preserves_store_camera_and_role(self) -> None:
        original_upload_dir = dashboard_server.UPLOAD_DIR
        original_registry_path = dashboard_server.UPLOAD_REGISTRY_PATH
        original_media_roots = list(dashboard_server.MEDIA_ROOTS)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            video_path = tmp_dir / "guided.mp4"
            video_path.write_bytes(b"fake video bytes")
            dashboard_server.UPLOAD_DIR = tmp_dir
            dashboard_server.UPLOAD_REGISTRY_PATH = tmp_dir / "sources.json"
            dashboard_server.MEDIA_ROOTS = [*original_media_roots, tmp_dir.resolve()]
            source = dashboard_server.source_payload(
                path=video_path,
                store_id="STORE_GUIDED",
                camera_id="CAM_5",
                role="billing",
                layout_path=PROJECT_ROOT / "contracts" / "store_layout.json",
                label="Guided billing test",
                created_at="2026-06-15T00:00:00Z",
                session_label="Guided billing test",
            )
            dashboard_server.register_upload_source(source)

            try:
                discovered = {
                    row["source_id"]: row
                    for row in dashboard_server.known_sources()
                }[source["source_id"]]
                resolved = resolve_source(
                    LiveSessionRequest(
                        source_id=source["source_id"],
                        session_label="Run from upload",
                    )
                )
            finally:
                dashboard_server.UPLOAD_DIR = original_upload_dir
                dashboard_server.UPLOAD_REGISTRY_PATH = original_registry_path
                dashboard_server.MEDIA_ROOTS = original_media_roots

        self.assertEqual(discovered["store_id"], "STORE_GUIDED")
        self.assertEqual(discovered["camera_id"], "CAM_5")
        self.assertEqual(discovered["role"], "billing")
        self.assertEqual(resolved["store_id"], "STORE_GUIDED")
        self.assertEqual(resolved["camera_id"], "CAM_5")
        self.assertEqual(resolved["role"], "billing")
        self.assertEqual(resolved["label"], "Run from upload")

    def test_phase4_layout_readiness_handles_full_and_detection_only_modes(self) -> None:
        ready = dashboard_server.layout_readiness(
            PROJECT_ROOT / "contracts" / "store_layout.json",
            "CAM_5",
            "billing",
            "billing",
        )
        missing = dashboard_server.layout_readiness(
            PROJECT_ROOT / "contracts" / "missing_layout.json",
            "CAM_9",
            "zone",
            "full_store",
        )

        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["effective_analysis_mode"], "billing")
        self.assertIn("billing", ready["supported_analysis_modes"])
        self.assertEqual(missing["status"], "detection_only")
        self.assertEqual(missing["effective_analysis_mode"], "detection_only")
        self.assertTrue(missing["warnings"])

    def test_phase4_source_payload_carries_upload_metadata_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "upload.mp4"
            video_path.write_bytes(b"placeholder")
            source = dashboard_server.source_payload(
                path=video_path,
                store_id="STORE_BLR_002",
                camera_id="CAM_3",
                role="entry",
                layout_path=PROJECT_ROOT / "contracts" / "store_layout.json",
                label="Entry upload",
                original_filename="entry.mp4",
                video_metadata={"readable": True, "duration_seconds": 12.5, "fps": 25},
                requested_analysis_mode="entry_exit",
            )

        self.assertEqual(source["original_filename"], "entry.mp4")
        self.assertEqual(source["video_metadata"]["duration_seconds"], 12.5)
        self.assertEqual(source["recommended_analysis_mode"], "entry_exit")
        self.assertIn("entry_exit", source["analysis_capabilities"])
        self.assertEqual(source["layout_readiness"]["status"], "ready")

    def test_phase4_session_progress_exposes_operator_steps(self) -> None:
        progress = dashboard_server.session_progress("detection", "processing")
        complete = dashboard_server.session_progress("complete", "complete")
        failed = dashboard_server.session_progress("failed", "failed", "bad video")

        self.assertEqual(progress["percent"], 45)
        self.assertTrue(any(step["status"] == "current" and step["key"] == "detection" for step in progress["steps"]))
        self.assertEqual(complete["percent"], 100)
        self.assertTrue(all(step["status"] == "complete" for step in complete["steps"]))
        self.assertEqual(failed["error"], "bad video")
        self.assertTrue(any(step["status"] == "failed" for step in failed["steps"]))


if __name__ == "__main__":
    unittest.main()
