from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import cv2
except ImportError as exc:  # pragma: no cover - dependency setup guard.
    raise RuntimeError(
        "OpenCV is not installed. Run `python -m pip install -r requirements.txt`."
    ) from exc


DEFAULT_VIDEO_DIR = Path(
    r"D:\code\purplletech\CCTV Footage-20260529T160731Z-3-00144614ea\CCTV Footage"
)

ROLE_GUESSES = {
    "CAM_1": {
        "role_guess": "sales_floor_top_wall",
        "zones_to_calibrate": ["TOP_WALL_SKINCARE", "MAKEUP_UNIT", "FOH"],
        "notes": "Wide view of skincare/top wall brands and central display. Useful for product-zone visits and dwell.",
    },
    "CAM_2": {
        "role_guess": "sales_floor_bottom_wall",
        "zones_to_calibrate": ["BOTTOM_WALL_MAKEUP", "ACCESSORIES_PMU", "MAKEUP_UNIT", "FOH"],
        "notes": "Wide view of makeup/bottom wall brands and central floor. Useful for product-zone visits and staff/customer movement.",
    },
    "CAM_3": {
        "role_guess": "entry_exit",
        "zones_to_calibrate": ["ENTRY_EXIT"],
        "notes": "Door threshold and mall corridor view. Highest-priority camera for ENTRY, EXIT, and REENTRY events.",
    },
    "CAM_4": {
        "role_guess": "stock_or_staff_area",
        "zones_to_calibrate": [],
        "notes": "Back-room/stock style view. Low priority for customer funnel; may help document staff/non-customer area exclusion.",
    },
    "CAM_5": {
        "role_guess": "billing_counter",
        "zones_to_calibrate": ["BILLING_COUNTER", "ACCESSORIES_PMU"],
        "notes": "Cash counter and checkout area. Highest-priority camera for billing queue and POS correlation.",
    },
}


def camera_id_from_path(path: Path) -> str:
    digits = "".join(ch for ch in path.stem if ch.isdigit())
    return f"CAM_{digits}" if digits else path.stem.upper().replace(" ", "_")


def read_video_metadata(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration_seconds = frame_count / fps if fps > 0 else 0.0
    capture.release()

    return {
        "camera_id": camera_id_from_path(path),
        "filename": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_seconds": round(duration_seconds, 3),
    }


def extract_frame(path: Path, timestamp_seconds: float, output_path: Path) -> bool:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    target_frame = int(timestamp_seconds * fps) if fps > 0 else 0
    if frame_count:
        target_frame = max(0, min(target_frame, frame_count - 1))
    capture.set(cv2.CAP_PROP_POS_FRAMES, target_frame)

    ok, frame = capture.read()
    capture.release()
    if not ok:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(output_path), frame))


def create_contact_sheet(image_paths: list[Path], output_path: Path) -> None:
    frames = []
    for image_path in image_paths:
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        image = cv2.resize(image, (480, 270))
        cv2.putText(
            image,
            image_path.stem,
            (16, 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        frames.append(image)

    if not frames:
        raise RuntimeError("No frames available for contact sheet.")

    blank = frames[0] * 0
    while len(frames) < 6:
        frames.append(blank.copy())

    top = cv2.hconcat(frames[:3])
    bottom = cv2.hconcat(frames[3:6])
    sheet = cv2.vconcat([top, bottom])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), sheet)


def build_calibration_template(metadata: list[dict]) -> dict:
    return {
        "source": "Phase 4 reference-frame extraction",
        "instructions": [
            "Open calibration/reference_frames/contact_sheet.jpg to identify each camera view.",
            "Open each full-size reference frame to mark zone polygons.",
            "Replace placeholder polygons in contracts/store_layout.json after visual calibration.",
            "Use normalized coordinates: x = pixel_x / frame_width, y = pixel_y / frame_height.",
        ],
        "cameras": [
            {
                "camera_id": item["camera_id"],
                "filename": item["filename"],
                "resolution": {"width": item["width"], "height": item["height"]},
                "fps": item["fps"],
                "duration_seconds": item["duration_seconds"],
                "reference_frame": f"calibration/reference_frames/{item['camera_id']}_t10s.jpg",
                "role_guess": ROLE_GUESSES.get(item["camera_id"], {}).get("role_guess", "unknown"),
                "zones_to_calibrate": ROLE_GUESSES.get(item["camera_id"], {}).get("zones_to_calibrate", []),
                "notes": ROLE_GUESSES.get(item["camera_id"], {}).get(
                    "notes",
                    "Fill this after inspecting the extracted frame.",
                ),
            }
            for item in metadata
        ],
    }


def write_camera_role_notes(metadata: list[dict], output_path: Path) -> None:
    lines = [
        "# Camera Role Notes",
        "",
        "These notes are based on the Phase 4 extracted reference frames.",
        "",
    ]
    for item in metadata:
        guess = ROLE_GUESSES.get(item["camera_id"], {})
        lines.extend(
            [
                f"## {item['camera_id']} - {guess.get('role_guess', 'unknown')}",
                "",
                f"- File: `{item['filename']}`",
                f"- Resolution: `{item['width']}x{item['height']}`",
                f"- FPS: `{item['fps']}`",
                f"- Duration: `{item['duration_seconds']}s`",
                f"- Zones to calibrate: `{', '.join(guess.get('zones_to_calibrate', [])) or 'none'}`",
                f"- Notes: {guess.get('notes', 'Fill after inspection.')}",
                "",
            ]
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract reference frames from CCTV clips.")
    parser.add_argument("--video-dir", type=Path, default=DEFAULT_VIDEO_DIR)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "calibration" / "reference_frames",
    )
    parser.add_argument("--timestamp-seconds", type=float, default=10.0)
    args = parser.parse_args()

    video_paths = sorted(args.video_dir.glob("CAM *.mp4"))
    if not video_paths:
        raise FileNotFoundError(f"No CAM *.mp4 videos found in {args.video_dir}")

    metadata = []
    extracted_paths = []
    for video_path in video_paths:
        item = read_video_metadata(video_path)
        metadata.append(item)
        output_path = args.output_dir / f"{item['camera_id']}_t{int(args.timestamp_seconds)}s.jpg"
        if not extract_frame(video_path, args.timestamp_seconds, output_path):
            raise RuntimeError(f"Failed to extract frame from {video_path}")
        item["reference_frame"] = str(output_path)
        extracted_paths.append(output_path)
        print(
            f"{item['camera_id']}: {item['width']}x{item['height']} "
            f"{item['fps']}fps {item['duration_seconds']}s -> {output_path}"
        )

    contact_sheet = args.output_dir / "contact_sheet.jpg"
    create_contact_sheet(extracted_paths, contact_sheet)

    inventory_path = PROJECT_ROOT / "calibration" / "camera_inventory.json"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    template_path = PROJECT_ROOT / "calibration" / "camera_calibration_template.json"
    template_path.write_text(json.dumps(build_calibration_template(metadata), indent=2), encoding="utf-8")

    notes_path = PROJECT_ROOT / "calibration" / "camera_role_notes.md"
    write_camera_role_notes(metadata, notes_path)

    print(f"Saved contact sheet: {contact_sheet}")
    print(f"Saved inventory: {inventory_path}")
    print(f"Saved calibration template: {template_path}")
    print(f"Saved camera role notes: {notes_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
