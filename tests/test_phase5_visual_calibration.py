from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import HTTPException

from dashboard import server as dashboard_server
from dashboard.server import CalibrationSaveRequest, CalibrationZonePayload


class Phase5VisualCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_dir = dashboard_server.CALIBRATION_DIR
        self.original_layout_dir = dashboard_server.CALIBRATION_LAYOUT_DIR
        self.original_frame_dir = dashboard_server.CALIBRATION_FRAME_DIR
        self.original_registry = dashboard_server.CALIBRATION_REGISTRY_PATH
        self.original_layout_roots = list(dashboard_server.LAYOUT_ROOTS)

    def tearDown(self) -> None:
        dashboard_server.CALIBRATION_DIR = self.original_dir
        dashboard_server.CALIBRATION_LAYOUT_DIR = self.original_layout_dir
        dashboard_server.CALIBRATION_FRAME_DIR = self.original_frame_dir
        dashboard_server.CALIBRATION_REGISTRY_PATH = self.original_registry
        dashboard_server.LAYOUT_ROOTS = self.original_layout_roots

    def configure_temp_calibration_paths(self, tmp: Path) -> Path:
        base_layout = tmp / "base_layout.json"
        base_layout.write_text(
            json.dumps(
                {
                    "store_id": "STORE_TEST",
                    "store_name": "Test Store",
                    "timezone": "Asia/Kolkata",
                    "cameras": [
                        {
                            "camera_id": "CAM_1",
                            "role": "zone",
                            "description": "Test camera",
                            "zones_normalized": {},
                        }
                    ],
                    "zones": [],
                }
            ),
            encoding="utf-8",
        )
        dashboard_server.CALIBRATION_DIR = tmp / "calibrations"
        dashboard_server.CALIBRATION_LAYOUT_DIR = dashboard_server.CALIBRATION_DIR / "layouts"
        dashboard_server.CALIBRATION_FRAME_DIR = dashboard_server.CALIBRATION_DIR / "reference_frames"
        dashboard_server.CALIBRATION_REGISTRY_PATH = dashboard_server.CALIBRATION_DIR / "registry.json"
        dashboard_server.LAYOUT_ROOTS = [tmp.resolve(), dashboard_server.CALIBRATION_LAYOUT_DIR.resolve()]
        return base_layout

    def test_save_calibration_creates_versioned_layout_without_mutating_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_layout = self.configure_temp_calibration_paths(Path(tmpdir))
            request = CalibrationSaveRequest(
                store_id="STORE_TEST",
                camera_id="CAM_1",
                camera_role="zone",
                base_layout_path=str(base_layout),
                zones=[
                    CalibrationZonePayload(
                        zone_id="zone makeup",
                        label="Makeup Wall",
                        kind="product_zone",
                        sku_zone="MAKEUP",
                        polygon_normalized=[[0.1, 0.1], [0.5, 0.1], [0.5, 0.6], [0.1, 0.6]],
                    )
                ],
                notes="unit test",
            )

            result = dashboard_server.save_calibration_layout(request)
            version = result["version"]
            saved_layout = json.loads(Path(version["layout_path"]).read_text(encoding="utf-8"))
            base_after = json.loads(base_layout.read_text(encoding="utf-8"))
            versions = dashboard_server.list_calibration_versions("STORE_TEST", "CAM_1")

        self.assertEqual(version["store_id"], "STORE_TEST")
        self.assertEqual(version["camera_id"], "CAM_1")
        self.assertEqual(version["zone_ids"], ["ZONE_MAKEUP"])
        self.assertEqual(len(versions), 1)
        self.assertIn("ZONE_MAKEUP", saved_layout["cameras"][0]["zones_normalized"])
        self.assertEqual(saved_layout["zones"][0]["camera_ids"], ["CAM_1"])
        self.assertEqual(base_after["cameras"][0]["zones_normalized"], {})

    def test_save_calibration_supports_entry_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_layout = self.configure_temp_calibration_paths(Path(tmpdir))
            request = CalibrationSaveRequest(
                store_id="STORE_TEST",
                camera_id="CAM_1",
                camera_role="entry",
                base_layout_path=str(base_layout),
                entry_line_normalized=[[0.25, 0.1], [0.25, 0.9]],
            )

            result = dashboard_server.save_calibration_layout(request)
            saved_layout = json.loads(Path(result["version"]["layout_path"]).read_text(encoding="utf-8"))

        self.assertEqual(saved_layout["cameras"][0]["role"], "entry")
        self.assertEqual(saved_layout["cameras"][0]["entry_line_normalized"], [[0.25, 0.1], [0.25, 0.9]])
        self.assertTrue(result["version"]["has_entry_line"])

    def test_discovered_sources_prefer_latest_calibration_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            base_layout = self.configure_temp_calibration_paths(tmp)
            video_path = tmp / "camera.mp4"
            video_path.write_bytes(b"placeholder")
            request = CalibrationSaveRequest(
                store_id="STORE_TEST",
                camera_id="CAM_1",
                camera_role="zone",
                base_layout_path=str(base_layout),
                zones=[
                    CalibrationZonePayload(
                        zone_id="ZONE_TEST",
                        label="Test Zone",
                        kind="product_zone",
                        polygon_normalized=[[0.1, 0.1], [0.8, 0.1], [0.8, 0.8]],
                    )
                ],
            )
            version = dashboard_server.save_calibration_layout(request)["version"]
            sources: list[dict] = []
            dashboard_server.add_source(sources, video_path, "STORE_TEST", "CAM_1", "zone", base_layout)

        self.assertEqual(sources[0]["layout_path"], version["layout_path"])
        self.assertEqual(sources[0]["layout_readiness"]["status"], "ready")

    def test_calibration_rejects_invalid_normalized_points(self) -> None:
        with self.assertRaises(HTTPException):
            dashboard_server.validate_polygon([[0.1, 0.1], [1.2, 0.2], [0.2, 0.8]], "BAD_ZONE")


if __name__ == "__main__":
    unittest.main()
