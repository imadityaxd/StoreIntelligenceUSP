from __future__ import annotations

import unittest
from pathlib import Path

from pipeline.detect import external_tracker_assignment_decision, normalize_tracker_backend
from pipeline.overlay import build_overlay_frame
from pipeline.timebase import load_time_offsets
from pipeline.tracker import CentroidTracker, Detection, ExternalDetection, ExternalIdTracker, Track
from pipeline.zones import load_layout


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Phase9TrackingPersistenceTests(unittest.TestCase):
    def test_tracker_backend_normalization_supports_usp_modes(self) -> None:
        self.assertEqual(normalize_tracker_backend(None), "auto")
        self.assertEqual(normalize_tracker_backend("auto"), "auto")
        self.assertEqual(normalize_tracker_backend("ByteTrack"), "bytetrack")
        self.assertEqual(normalize_tracker_backend("BoT-SORT"), "botsort")
        self.assertEqual(normalize_tracker_backend("simple"), "centroid")
        with self.assertRaises(ValueError):
            normalize_tracker_backend("unknown")

    def test_external_tracker_allows_id_assignment_warmup(self) -> None:
        action, streak = external_tracker_assignment_decision(
            raw_detection_count=2,
            assigned_detection_count=0,
            unassigned_streak=0,
            warmup_samples=5,
        )
        self.assertEqual((action, streak), ("warmup", 1))

        action, streak = external_tracker_assignment_decision(
            raw_detection_count=2,
            assigned_detection_count=0,
            unassigned_streak=4,
            warmup_samples=5,
        )
        self.assertEqual((action, streak), ("fallback", 5))

        action, streak = external_tracker_assignment_decision(
            raw_detection_count=2,
            assigned_detection_count=1,
            unassigned_streak=4,
            warmup_samples=5,
        )
        self.assertEqual((action, streak), ("external", 0))

        action, streak = external_tracker_assignment_decision(
            raw_detection_count=2,
            assigned_detection_count=0,
            unassigned_streak=4,
            has_assigned_ids=True,
            warmup_samples=5,
        )
        self.assertEqual((action, streak), ("external", 0))

    def test_tracker_keeps_same_id_through_short_detector_gap(self) -> None:
        tracker = CentroidTracker(max_distance=45, max_missed=4)

        tracks, ended = tracker.update([Detection(100, 100, 50, 120, 0.90)], frame_index=0)
        self.assertFalse(ended)
        track_id = tracks[0].track_id

        tracks, ended = tracker.update([Detection(130, 100, 50, 120, 0.88)], frame_index=5)
        self.assertFalse(ended)
        self.assertEqual(tracks[0].track_id, track_id)
        self.assertGreater(tracks[0].velocity_x, 0)

        tracks, ended = tracker.update([], frame_index=10)
        self.assertFalse(ended)
        self.assertEqual(tracks[0].track_id, track_id)
        self.assertEqual(tracks[0].missed, 1)
        self.assertGreater(tracks[0].detection.x, 130)

        tracks, ended = tracker.update([Detection(190, 100, 50, 120, 0.86)], frame_index=15)
        self.assertFalse(ended)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].track_id, track_id)
        self.assertEqual(tracks[0].missed, 0)
        self.assertEqual(tracks[0].hits, 3)

    def test_tracker_ends_track_after_grace_window(self) -> None:
        tracker = CentroidTracker(max_distance=45, max_missed=2)

        tracks, _ended = tracker.update([Detection(100, 100, 50, 120, 0.90)], frame_index=0)
        track_id = tracks[0].track_id
        tracker.update([], frame_index=5)
        tracker.update([], frame_index=10)
        tracks, ended = tracker.update([], frame_index=15)

        self.assertEqual(tracks, [])
        self.assertEqual([track.track_id for track in ended], [track_id])

    def test_overlay_includes_predicted_tracks_inside_grace_window(self) -> None:
        layout = load_layout(PROJECT_ROOT / "contracts" / "store_layout.json")
        cameras = {camera["camera_id"]: camera for camera in layout["cameras"]}
        time_config = load_time_offsets(PROJECT_ROOT / "contracts" / "camera_time_offsets.json")
        track = Track(
            track_id=11,
            detection=Detection(100, 120, 80, 220, 0.72),
            first_frame=0,
            last_frame=60,
            missed=4,
            hits=5,
            last_observed_frame=48,
        )

        frame = build_overlay_frame(
            session_id="SESSION_TRACKING",
            camera_id="CAM_1",
            camera_config=cameras["CAM_1"],
            time_config=time_config,
            frame_index=60,
            fps=30.0,
            frame_width=1920,
            frame_height=1080,
            tracks=[track],
            max_missed=8,
        )

        self.assertEqual(len(frame["tracks"]), 1)
        self.assertEqual(frame["tracks"][0]["track_status"], "predicted")
        self.assertEqual(frame["tracks"][0]["stale_samples"], 4)
        self.assertEqual(frame["tracks"][0]["stale_seconds"], 0.4)

    def test_overlay_drops_predicted_tracks_after_grace_window(self) -> None:
        layout = load_layout(PROJECT_ROOT / "contracts" / "store_layout.json")
        cameras = {camera["camera_id"]: camera for camera in layout["cameras"]}
        time_config = load_time_offsets(PROJECT_ROOT / "contracts" / "camera_time_offsets.json")
        track = Track(
            track_id=12,
            detection=Detection(100, 120, 80, 220, 0.72),
            first_frame=0,
            last_frame=90,
            missed=9,
            hits=5,
            last_observed_frame=48,
        )

        frame = build_overlay_frame(
            session_id="SESSION_TRACKING",
            camera_id="CAM_1",
            camera_config=cameras["CAM_1"],
            time_config=time_config,
            frame_index=90,
            fps=30.0,
            frame_width=1920,
            frame_height=1080,
            tracks=[track],
            max_missed=8,
        )

        self.assertEqual(frame["tracks"], [])

    def test_external_id_tracker_preserves_detector_supplied_ids(self) -> None:
        tracker = ExternalIdTracker(max_missed=3)

        tracks, ended = tracker.update(
            [ExternalDetection(track_id=42, detection=Detection(100, 100, 50, 120, 0.90))],
            frame_index=0,
        )
        self.assertFalse(ended)
        self.assertEqual(tracks[0].track_id, 42)

        tracks, ended = tracker.update([], frame_index=5)
        self.assertFalse(ended)
        self.assertEqual(tracks[0].track_id, 42)
        self.assertEqual(tracks[0].missed, 1)

        tracks, ended = tracker.update(
            [ExternalDetection(track_id=42, detection=Detection(130, 100, 50, 120, 0.88))],
            frame_index=10,
        )
        self.assertFalse(ended)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].track_id, 42)
        self.assertEqual(tracks[0].missed, 0)
        self.assertEqual(tracks[0].hits, 2)


if __name__ == "__main__":
    unittest.main()
