from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def load_layout(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def polygon_to_pixels(polygon_normalized: list[list[float]], frame_width: int, frame_height: int) -> np.ndarray:
    points = [
        [int(round(point[0] * frame_width)), int(round(point[1] * frame_height))]
        for point in polygon_normalized
    ]
    return np.array(points, dtype=np.int32)


def point_in_polygon(point: tuple[int, int], polygon_pixels: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon_pixels, point, False) >= 0


def zones_for_point(
    camera_config: dict[str, Any],
    point: tuple[int, int],
    frame_width: int,
    frame_height: int,
) -> list[str]:
    zone_ids: list[str] = []
    for zone_id, polygon in camera_config.get("zones_normalized", {}).items():
        poly_pixels = polygon_to_pixels(polygon, frame_width, frame_height)
        if point_in_polygon(point, poly_pixels):
            zone_ids.append(zone_id)
    return zone_ids
