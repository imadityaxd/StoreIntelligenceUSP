"""
YOLO-only frame audit for retail CCTV detection quality.

This script samples already-extracted reference frames and reports YOLO person
counts, confidence distributions, and low-confidence frames that need manual
review. It does not run or compare MOG2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.detect_yolo import YOLODetector


def audit_yolo(frame_path: Path, detector: YOLODetector) -> dict:
    frame = cv2.imread(str(frame_path))
    if frame is None:
        return {"frame": frame_path.name, "error": "could_not_read_frame"}

    detections = detector.detect_frame(frame)
    confidences = [float(detection["confidence"]) for detection in detections]
    return {
        "frame": frame_path.name,
        "yolo_count": len(detections),
        "yolo_confidences": confidences,
        "yolo_avg_conf": round(mean(confidences), 3) if confidences else 0.0,
        "yolo_min_conf": round(min(confidences), 3) if confidences else 0.0,
        "needs_manual_review": len(detections) == 0 or any(conf < 0.4 for conf in confidences),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="YOLO-only frame audit.")
    parser.add_argument("--frames", type=Path, default=Path("data/reference_frames"))
    parser.add_argument("--yolo-conf", type=float, default=0.40)
    parser.add_argument("--yolo-iou", type=float, default=0.28)
    parser.add_argument("--yolo-imgsz", type=int, default=960)
    parser.add_argument("--yolo-model-path", type=Path, default=PROJECT_ROOT / "yolov8s.pt")
    parser.add_argument("--report", type=Path, default=Path("data/reports/detection_yolo_audit.json"))
    args = parser.parse_args()

    if not args.frames.exists():
        raise FileNotFoundError(f"Frames directory not found: {args.frames}")

    frame_files = [
        path
        for path in sorted(args.frames.glob("*.jpg")) + sorted(args.frames.glob("*.png"))
        if path.stem.upper().startswith("CAM_")
    ]
    if not frame_files:
        raise FileNotFoundError(f"No CAM_*.jpg/CAM_*.png reference frames in {args.frames}")

    detector = YOLODetector(
        model_path=args.yolo_model_path,
        conf_threshold=args.yolo_conf,
        iou_threshold=args.yolo_iou,
        imgsz=args.yolo_imgsz,
    )

    rows = [audit_yolo(frame_path, detector) for frame_path in frame_files]
    valid_rows = [row for row in rows if "error" not in row]
    counts = [int(row["yolo_count"]) for row in valid_rows]
    review_rows = [row["frame"] for row in valid_rows if row["needs_manual_review"]]

    report = {
        "detector": "YOLOv8",
        "model_path": str(args.yolo_model_path),
        "parameters": {
            "conf_threshold": args.yolo_conf,
            "iou_threshold": args.yolo_iou,
            "imgsz": args.yolo_imgsz,
        },
        "total_frames": len(rows),
        "readable_frames": len(valid_rows),
        "avg_person_count": round(mean(counts), 2) if counts else 0.0,
        "zero_detection_frames": [row["frame"] for row in valid_rows if row["yolo_count"] == 0],
        "manual_review_frames": review_rows,
        "frames": rows,
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "report": str(args.report),
        "total_frames": report["total_frames"],
        "avg_person_count": report["avg_person_count"],
        "manual_review_frames": len(review_rows),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
