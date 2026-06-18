from __future__ import annotations

import unittest

from pipeline.tracker_profiles import apply_auto_profile, profile_for_camera


class Phase11TrackerProfileTests(unittest.TestCase):
    def test_camera_profile_overrides_role_defaults(self) -> None:
        profile = profile_for_camera(store_id="STORE_BLR_002", camera_id="CAM_5", role="billing")

        self.assertEqual(profile["profile_version"], "TRACKER_PROFILES_V1")
        self.assertEqual(profile["profile_name"], "store1_billing_counter")
        self.assertEqual(profile["tracker_backend"], "botsort")
        self.assertEqual(profile["process_fps"], 4.0)
        self.assertEqual(profile["min_area"], 800)

    def test_auto_profile_controls_ml_knobs_but_keeps_requested_window(self) -> None:
        resolved = apply_auto_profile(
            store_id="STORE_BLR_002",
            camera_id="CAM_3",
            role="entry",
            tracker_backend="auto",
            process_fps=1.0,
            yolo_conf=0.9,
            yolo_iou=0.9,
            yolo_imgsz=320,
            min_area=10_000,
            max_seconds=140.0,
        )

        self.assertTrue(resolved["auto_profile_applied"])
        self.assertEqual(resolved["tracker_backend"], "centroid")
        self.assertEqual(resolved["process_fps"], 5.0)
        self.assertEqual(resolved["yolo_conf"], 0.35)
        self.assertEqual(resolved["min_area"], 700)
        self.assertEqual(resolved["max_seconds"], 140.0)

    def test_explicit_tracker_keeps_operator_overrides(self) -> None:
        resolved = apply_auto_profile(
            store_id="STORE_BLR_002",
            camera_id="CAM_5",
            role="billing",
            tracker_backend="centroid",
            process_fps=2.0,
            yolo_conf=0.5,
            yolo_iou=0.4,
            yolo_imgsz=640,
            min_area=1500,
            max_seconds=30,
        )

        self.assertFalse(resolved["auto_profile_applied"])
        self.assertEqual(resolved["tracker_backend"], "centroid")
        self.assertEqual(resolved["process_fps"], 2.0)
        self.assertEqual(resolved["yolo_conf"], 0.5)
        self.assertEqual(resolved["min_area"], 1500)


if __name__ == "__main__":
    unittest.main()
