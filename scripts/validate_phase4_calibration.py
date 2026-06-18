from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_PATH = PROJECT_ROOT / "contracts" / "store_layout.json"


REQUIRED_CAMERA_ROLES = {
    "CAM_1": "sales_floor_top_wall",
    "CAM_2": "sales_floor_bottom_wall",
    "CAM_3": "entry_exit",
    "CAM_5": "billing_counter",
}

REQUIRED_ZONES_BY_CAMERA = {
    "CAM_1": {"TOP_WALL_SKINCARE", "FOH"},
    "CAM_2": {"BOTTOM_WALL_MAKEUP", "FOH"},
    "CAM_3": {"ENTRY_EXIT"},
    "CAM_5": {"BILLING_COUNTER"},
}


def check_polygon_normalized(zone_id: str, polygon: list[list[float]]) -> list[str]:
    errors: list[str] = []
    if len(polygon) < 3:
        errors.append(f"{zone_id}: polygon has fewer than 3 points")
        return errors
    for point in polygon:
        if len(point) != 2:
            errors.append(f"{zone_id}: point {point} is not [x, y]")
            continue
        x, y = point
        if not (0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0):
            errors.append(f"{zone_id}: point {point} is outside normalized range [0,1]")
    return errors


def main() -> int:
    layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    cameras: dict = layout["cameras"]
    errors: list[str] = []

    if "phase4b" not in layout.get("calibration_status", ""):
        errors.append("calibration_status must mention phase4b")

    for camera_id, expected_role in REQUIRED_CAMERA_ROLES.items():
        camera = cameras.get(camera_id)
        if not camera:
            errors.append(f"missing camera: {camera_id}")
            continue
        if camera.get("role") != expected_role:
            errors.append(f"{camera_id}: expected role '{expected_role}', got '{camera.get('role')}'")
        zones = camera.get("zones_normalized", {})
        required_zones = REQUIRED_ZONES_BY_CAMERA[camera_id]
        missing = required_zones - set(zones.keys())
        if missing:
            errors.append(f"{camera_id}: missing required zones {sorted(missing)}")
        for zone_id, polygon in zones.items():
            errors.extend([f"{camera_id}: {msg}" for msg in check_polygon_normalized(zone_id, polygon)])

    cam3 = cameras.get("CAM_3", {})
    entry_line = cam3.get("entry_line_normalized")
    if not isinstance(entry_line, list) or len(entry_line) != 2:
        errors.append("CAM_3: entry_line_normalized must contain two points")
    else:
        for point in entry_line:
            if len(point) != 2:
                errors.append(f"CAM_3: invalid entry line point {point}")
            else:
                x, y = point
                if not (0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0):
                    errors.append(f"CAM_3: entry line point {point} is outside normalized range [0,1]")

    if errors:
        print("Phase 4 calibration validation failed")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Phase 4 calibration validation passed")
    print("- roles and required zones present for CAM_1/CAM_2/CAM_3/CAM_5")
    print("- polygons and entry line are normalized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
