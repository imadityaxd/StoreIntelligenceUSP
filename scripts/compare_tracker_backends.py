from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.detect import normalize_tracker_backend
from pipeline.detect_yolo import YOLODetector
from pipeline.tracker import CentroidTracker, Detection, ExternalDetection, ExternalIdTracker
from pipeline.tracker_profiles import apply_auto_profile


def _frame_detections(raw: list[dict[str, Any]], min_area: int) -> list[Detection]:
    detections: list[Detection] = []
    for row in raw:
        x1, y1, x2, y2 = row["bbox"]
        w = x2 - x1
        h = y2 - y1
        if w * h < min_area:
            continue
        detections.append(Detection(x=x1, y=y1, w=w, h=h, confidence=row["confidence"]))
    return detections


def compare_backend(
    *,
    video_path: Path,
    store_id: str,
    camera_id: str,
    role: str,
    backend: str,
    model_path: Path,
    max_seconds: float,
) -> dict[str, Any]:
    resolved = apply_auto_profile(
        store_id=store_id,
        camera_id=camera_id,
        role=role,
        tracker_backend=backend,
        process_fps=None,
        yolo_conf=None,
        yolo_iou=None,
        yolo_imgsz=None,
        min_area=None,
        max_seconds=max_seconds,
    )
    active_backend = str(resolved["tracker_backend"])
    detector = YOLODetector(
        model_path=model_path,
        conf_threshold=float(resolved["yolo_conf"]),
        iou_threshold=float(resolved["yolo_iou"]),
        imgsz=int(resolved["yolo_imgsz"]),
    )

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    process_fps = float(resolved["process_fps"])
    sample_every = max(1, int(round(fps / process_fps))) if fps and process_fps else 1
    max_frames = int(max_seconds * fps) if fps and max_seconds else None
    centroid = CentroidTracker(max_distance=125, max_missed=18)
    external = ExternalIdTracker(max_missed=18)

    frames_sampled = 0
    detections_total = 0
    assigned_ids_total = 0
    track_counts: list[int] = []
    unique_ids: set[int] = set()
    predicted_samples = 0
    frame_index = -1
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_index += 1
            if max_frames is not None and frame_index > max_frames:
                break
            if frame_index % sample_every != 0:
                continue

            frames_sampled += 1
            if active_backend in {"bytetrack", "botsort"}:
                tracker_config = "bytetrack.yaml" if active_backend == "bytetrack" else "botsort.yaml"
                raw = detector.track_frame(frame, tracker_config=tracker_config, persist=True)
                ext_rows: list[ExternalDetection] = []
                for row in raw:
                    tracker_id = row.get("tracker_id")
                    x1, y1, x2, y2 = row["bbox"]
                    w = x2 - x1
                    h = y2 - y1
                    if w * h >= int(resolved["min_area"]) and tracker_id is not None:
                        detection = Detection(x=x1, y=y1, w=w, h=h, confidence=row["confidence"])
                        ext_rows.append(ExternalDetection(track_id=int(tracker_id), detection=detection))
                tracks, _ended = external.update(ext_rows, frame_index)
                assigned_ids_total += len(ext_rows)
                detections_total += len(raw)
            else:
                raw = detector.detect_frame(frame)
                detections = _frame_detections(raw, int(resolved["min_area"]))
                tracks, _ended = centroid.update(detections, frame_index)
                assigned_ids_total += len(detections)
                detections_total += len(raw)

            track_counts.append(len(tracks))
            predicted_samples += sum(1 for track in tracks if track.missed > 0)
            unique_ids.update(track.track_id for track in tracks)
    finally:
        capture.release()

    avg_tracks = mean(track_counts) if track_counts else 0.0
    return {
        "backend_requested": backend,
        "backend_active": active_backend,
        "profile": resolved["profile"],
        "frames_sampled": frames_sampled,
        "detections_total": detections_total,
        "assigned_ids_total": assigned_ids_total,
        "unique_track_count": len(unique_ids),
        "avg_tracks_per_sampled_frame": round(avg_tracks, 3),
        "max_tracks_in_frame": max(track_counts) if track_counts else 0,
        "predicted_track_samples": predicted_samples,
        "tracker_id_assignment_rate": round(min(1.0, assigned_ids_total / detections_total), 3) if detections_total else 0.0,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Tracker Backend Comparison",
        "",
        f"- Video: `{report['video_path']}`",
        f"- Store: `{report['store_id']}`",
        f"- Camera: `{report['camera_id']}`",
        f"- Role: `{report['role']}`",
        f"- Max seconds: `{report['max_seconds']}`",
        "",
        "## Results",
        "",
    ]
    for row in report["results"]:
        lines.extend(
            [
                f"### {row['backend_requested']}",
                f"- Active backend: `{row['backend_active']}`",
                f"- Profile: `{row['profile']['profile_name']}`",
                f"- Frames sampled: {row['frames_sampled']}",
                f"- Detections: {row['detections_total']}",
                f"- Assigned IDs: {row['assigned_ids_total']}",
                f"- Unique tracks: {row['unique_track_count']}",
                f"- Avg tracks/frame: {row['avg_tracks_per_sampled_frame']}",
                f"- Tracker ID assignment rate: {row['tracker_id_assignment_rate']}",
                f"- Predicted track samples: {row['predicted_track_samples']}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare tracker backends on a CCTV clip without ingesting events.")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--store-id", default="STORE_BLR_002")
    parser.add_argument("--camera-id", default="CAM_1")
    parser.add_argument("--role", default="zone")
    parser.add_argument("--backends", nargs="+", default=["auto", "bytetrack", "botsort", "centroid"])
    parser.add_argument("--max-seconds", type=float, default=20.0)
    parser.add_argument("--model-path", type=Path, default=PROJECT_ROOT / "yolov8s.pt")
    parser.add_argument("--report-json", type=Path, default=PROJECT_ROOT / "data" / "reports" / "tracker_backend_comparison.json")
    parser.add_argument("--report-md", type=Path, default=PROJECT_ROOT / "data" / "reports" / "tracker_backend_comparison.md")
    args = parser.parse_args()

    results = [
        compare_backend(
            video_path=args.video,
            store_id=args.store_id,
            camera_id=args.camera_id,
            role=args.role,
            backend=normalize_tracker_backend(backend),
            model_path=args.model_path,
            max_seconds=args.max_seconds,
        )
        for backend in args.backends
    ]
    report = {
        "video_path": str(args.video),
        "store_id": args.store_id,
        "camera_id": args.camera_id,
        "role": args.role,
        "max_seconds": args.max_seconds,
        "results": results,
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, args.report_md)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
