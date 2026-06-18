from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_PATH = PROJECT_ROOT / "contracts" / "store_layout.json"
REFERENCE_DIR = PROJECT_ROOT / "calibration" / "reference_frames"


def load_layout(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_layout(path: Path, layout: dict) -> None:
    path.write_text(json.dumps(layout, indent=2), encoding="utf-8")


def normalized(points: list[tuple[int, int]], width: int, height: int) -> list[list[float]]:
    return [[round(x / width, 6), round(y / height, 6)] for x, y in points]


def run_picker(image_path: Path, camera_id: str, zone_id: str) -> list[tuple[int, int]]:
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"Could not load image: {image_path}")

    points: list[tuple[int, int]] = []
    window_name = f"{camera_id}::{zone_id}"

    def on_click(event, x, y, flags, param):  # noqa: ANN001
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, on_click)

    help_lines = [
        "LMB: add point",
        "U: undo last point",
        "C: clear all points",
        "S: save polygon",
        "Q: quit without saving",
    ]

    while True:
        canvas = image.copy()
        for idx, (x, y) in enumerate(points):
            cv2.circle(canvas, (x, y), 6, (0, 255, 255), -1)
            cv2.putText(canvas, str(idx + 1), (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        if len(points) >= 2:
            for i in range(1, len(points)):
                cv2.line(canvas, points[i - 1], points[i], (0, 255, 0), 2)

        for idx, line in enumerate(help_lines):
            cv2.putText(canvas, line, (20, 30 + idx * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        cv2.imshow(window_name, canvas)
        key = cv2.waitKey(20) & 0xFF
        if key in (ord("q"), 27):
            cv2.destroyAllWindows()
            return []
        if key in (ord("u"), 8, 127) and points:
            points.pop()
        if key == ord("c"):
            points.clear()
        if key == ord("s"):
            if len(points) < 3:
                print("Need at least 3 points before saving.")
                continue
            cv2.destroyAllWindows()
            return points


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual polygon calibration helper for store_layout.json")
    parser.add_argument("--camera-id", required=True, help="Example: CAM_3")
    parser.add_argument("--zone-id", required=True, help="Example: ENTRY_EXIT")
    parser.add_argument(
        "--frame",
        type=Path,
        default=None,
        help="Optional explicit frame path. Defaults to calibration/reference_frames/<CAMERA>_t10s.jpg",
    )
    parser.add_argument("--layout", type=Path, default=LAYOUT_PATH)
    args = parser.parse_args()

    layout = load_layout(args.layout)
    camera = layout["cameras"].get(args.camera_id)
    if not camera:
        raise KeyError(f"Unknown camera_id: {args.camera_id}")

    frame_path = args.frame or (REFERENCE_DIR / f"{args.camera_id}_t10s.jpg")
    points = run_picker(frame_path, args.camera_id, args.zone_id)
    if not points:
        print("No polygon saved.")
        return 1

    image = cv2.imread(str(frame_path))
    h, w = image.shape[:2]
    poly = normalized(points, w, h)

    camera.setdefault("zones_normalized", {})
    camera["zones_normalized"][args.zone_id] = poly
    save_layout(args.layout, layout)
    print(f"Saved {args.zone_id} polygon for {args.camera_id}: {poly}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
