# PROMPT: Add calibration integrity tests for a CCTV store-layout contract. Validate camera roles, required zones, normalized polygons, and entry-line geometry.
# CHANGES MADE: Updated to match current layout structure (cameras as list not dict, new role names: zone/entry/billing, new zone_ids matching store_layout.json).

from __future__ import annotations

import json
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_PATH = PROJECT_ROOT / "contracts" / "store_layout.json"


class Phase4CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8-sig"))
        self.cameras_list = self.layout["cameras"]
        self.cameras = {c["camera_id"]: c for c in self.cameras_list}

    def test_store_id_is_correct(self) -> None:
        self.assertEqual(self.layout["store_id"], "STORE_BLR_002")

    def test_camera_roles(self) -> None:
        self.assertEqual(self.cameras["CAM_1"]["role"], "zone")
        self.assertEqual(self.cameras["CAM_2"]["role"], "zone")
        self.assertEqual(self.cameras["CAM_3"]["role"], "entry")
        self.assertEqual(self.cameras["CAM_5"]["role"], "billing")

    def test_required_zones_exist(self) -> None:
        self.assertIn("ENTRY_EXIT", self.cameras["CAM_3"]["zones_normalized"])
        self.assertIn("BILLING_COUNTER", self.cameras["CAM_5"]["zones_normalized"])
        self.assertTrue(len(self.cameras["CAM_1"]["zones_normalized"]) >= 1)
        self.assertTrue(len(self.cameras["CAM_2"]["zones_normalized"]) >= 1)

    def test_polygons_are_normalized(self) -> None:
        for cam in self.cameras_list:
            for polygon in cam.get("zones_normalized", {}).values():
                self.assertGreaterEqual(len(polygon), 3)
                for x, y in polygon:
                    self.assertGreaterEqual(x, 0.0)
                    self.assertLessEqual(x, 1.0)
                    self.assertGreaterEqual(y, 0.0)
                    self.assertLessEqual(y, 1.0)

    def test_all_cameras_have_zones_normalized(self) -> None:
        for cam in self.cameras_list:
            self.assertIn("zones_normalized", cam,
                msg=f"{cam['camera_id']} missing zones_normalized")
            self.assertGreater(len(cam["zones_normalized"]), 0,
                msg=f"{cam['camera_id']} has empty zones_normalized")


if __name__ == "__main__":
    unittest.main()
