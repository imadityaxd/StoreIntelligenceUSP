from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dashboard import server as dashboard_server
from pipeline.emit import EventEmitter
from pipeline.overlay import build_overlay_frame, read_overlay_jsonl, write_overlay_frame
from pipeline.timebase import load_time_offsets
from pipeline.tracker import Detection, Track
from pipeline.zones import load_layout


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Phase3OverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout_path = PROJECT_ROOT / "contracts" / "store_layout.json"
        self.layout = load_layout(self.layout_path)
        self.cameras = {camera["camera_id"]: camera for camera in self.layout["cameras"]}
        self.time_config = load_time_offsets(PROJECT_ROOT / "contracts" / "camera_time_offsets.json")

    def test_overlay_frame_contains_clamped_boxes_identity_and_zone(self) -> None:
        track = Track(
            track_id=7,
            detection=Detection(x=-10, y=20, w=250, h=320, confidence=0.876),
            first_frame=0,
            last_frame=30,
            hits=4,
        )

        frame = build_overlay_frame(
            session_id="SESSION_OVERLAY",
            camera_id="CAM_5",
            camera_config=self.cameras["CAM_5"],
            time_config=self.time_config,
            frame_index=30,
            fps=30.0,
            frame_width=1920,
            frame_height=1080,
            tracks=[track],
            visitor_id_for_track=lambda track_id: f"VIS_CAM_5_{track_id:05d}",
            staff_for_track=lambda _track_id, _zone_id: False,
        )

        self.assertEqual(frame["session_id"], "SESSION_OVERLAY")
        self.assertEqual(frame["video_time_seconds"], 1.0)
        self.assertEqual(frame["tracks"][0]["visitor_id"], "VIS_CAM_5_00007")
        self.assertEqual(frame["tracks"][0]["zone_id"], "BILLING_COUNTER")
        self.assertEqual(frame["tracks"][0]["bbox"], [0, 20, 240, 340])
        self.assertEqual(frame["tracks"][0]["person_type"], "person")
        self.assertIn("person", frame["tracks"][0]["label"])
        self.assertNotIn("customer", frame["tracks"][0]["label"])
        self.assertTrue(all(0 <= value <= 1 for value in frame["tracks"][0]["bbox_normalized"]))

    def test_overlay_frame_uses_staff_label_only_when_rule_marks_staff(self) -> None:
        track = Track(
            track_id=3,
            detection=Detection(x=40, y=50, w=180, h=420, confidence=0.91),
            first_frame=0,
            last_frame=0,
            hits=5,
        )

        frame = build_overlay_frame(
            session_id="SESSION_STAFF",
            camera_id="CAM_1",
            camera_config={**self.cameras["CAM_1"], "role": "staff-area"},
            time_config=self.time_config,
            frame_index=0,
            fps=30.0,
            frame_width=1920,
            frame_height=1080,
            tracks=[track],
            visitor_id_for_track=lambda track_id: f"VIS_STAFF_{track_id}",
            staff_for_track=lambda _track_id, _zone_id: True,
        )

        self.assertTrue(frame["tracks"][0]["is_staff"])
        self.assertEqual(frame["tracks"][0]["person_type"], "staff")
        self.assertIn("staff", frame["tracks"][0]["label"])

    def test_emitter_marks_staff_area_camera_as_staff(self) -> None:
        emitter = EventEmitter(
            "STORE_BLR_002",
            "CAM_4",
            {"camera_id": "CAM_4", "role": "staff-area", "zones_normalized": {}},
            fps=30.0,
            time_config=self.time_config,
        )

        self.assertTrue(emitter.is_staff_track(999, None))

    def test_overlay_jsonl_reader_skips_bad_partial_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlays.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                write_overlay_frame(handle, {"frame_index": 1, "tracks": []})
                handle.write("{bad partial line")

            rows = read_overlay_jsonl(path)

        self.assertEqual(rows, [{"frame_index": 1, "tracks": []}])

    def test_dashboard_overlay_payload_includes_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            overlay_path = Path(tmp) / "overlays.jsonl"
            events_path = Path(tmp) / "events.jsonl"
            rows = [
                {
                    "session_id": "SESSION_DASH",
                    "camera_id": "CAM_1",
                    "frame_index": 0,
                    "video_time_seconds": 0.0,
                    "frame_width": 1920,
                    "frame_height": 1080,
                    "tracks": [{"track_id": 1, "visitor_id": "VIS_1", "is_staff": False}],
                },
                {
                    "session_id": "SESSION_DASH",
                    "camera_id": "CAM_1",
                    "frame_index": 30,
                    "video_time_seconds": 1.0,
                    "frame_width": 1920,
                    "frame_height": 1080,
                    "tracks": [
                        {"track_id": 1, "visitor_id": "VIS_1", "is_staff": False},
                        {"track_id": 2, "visitor_id": "VIS_STAFF", "is_staff": True},
                    ],
                },
            ]
            overlay_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            events_path.write_text(
                json.dumps(
                    {
                        "visitor_id": "VIS_1",
                        "is_staff": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            session = {
                "session_id": "SESSION_DASH",
                "camera_id": "CAM_1",
                "overlay_path": str(overlay_path),
                "events_path": str(events_path),
                "source": {"layout_path": str(self.layout_path), "camera_id": "CAM_1"},
            }

            payload = dashboard_server.overlay_payload_for_session(session)

        self.assertTrue(payload["available"])
        self.assertEqual(payload["frame_count"], 2)
        self.assertEqual(payload["track_count"], 3)
        self.assertEqual(payload["unique_track_count"], 2)
        self.assertEqual(payload["person_track_count"], 2)
        self.assertEqual(payload["valid_person_track_count"], 2)
        self.assertEqual(payload["counted_person_track_count"], 1)
        self.assertEqual(payload["overlay_only_countable_track_count"], 1)
        self.assertEqual(payload["suspect_track_count"], 0)
        self.assertEqual(payload["staff_track_count"], 1)
        self.assertEqual(payload["first_video_time_seconds"], 0.0)
        self.assertEqual(payload["last_video_time_seconds"], 1.0)
        self.assertEqual(payload["coverage_seconds"], 1.0)
        self.assertGreaterEqual(len(payload["geometry"]["zones"]), 1)
        self.assertIn("polygon_normalized", payload["geometry"]["zones"][0])


if __name__ == "__main__":
    unittest.main()
