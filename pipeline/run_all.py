# pipeline/run_all.py
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.detect import run_detection


# Maps filename stem patterns to camera_id and role
STORE1_CAMERAS = [
    {"filename": "CAM 1 - zone.mp4",    "camera_id": "CAM_1", "role": "zone"},
    {"filename": "CAM 2 - zone.mp4",    "camera_id": "CAM_2", "role": "zone"},
    {"filename": "CAM 3 - entry.mp4",   "camera_id": "CAM_3", "role": "entry"},
    {"filename": "CAM 5 - billing.mp4", "camera_id": "CAM_5", "role": "billing"},
]

STORE2_CAMERAS = [
    {"filename": "entry 1.mp4",      "camera_id": "CAM_1", "role": "entry"},
    {"filename": "entry 2.mp4",      "camera_id": "CAM_2", "role": "entry"},
    {"filename": "zone.mp4",         "camera_id": "CAM_3", "role": "zone"},
    {"filename": "billing_area.mp4", "camera_id": "CAM_4", "role": "billing"},
]

STORE_CONFIGS = {
    "STORE_BLR_002": {
        "cameras": STORE1_CAMERAS,
        "layout": PROJECT_ROOT / "contracts" / "store_layout.json",
    },
    "STORE_BLR_003": {
        "cameras": STORE2_CAMERAS,
        "layout": PROJECT_ROOT / "contracts" / "store_layout_store2.json",
    },
}


def merge_jsonl(inputs: list[Path], output_path: Path) -> int:
    rows: list[dict] = []
    for path in inputs:
        if not path.exists():
            print(f"  [WARN] output not found, skipping: {path}")
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if raw:
                    rows.append(json.loads(raw))
    rows.sort(
        key=lambda row: (
            row["timestamp"],
            row["camera_id"],
            row["visitor_id"],
            row["metadata"]["session_seq"],
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return len(rows)


def run_store(
    store_id: str,
    session_id: str | None,
    video_dir: Path,
    out_dir: Path,
    merged_out: Path,
    time_offsets_path: Path,
    process_fps: float | None,
    max_seconds: float | None,
    min_area: int | None,
    yolo_conf: float | None,
    yolo_iou: float | None,
    yolo_imgsz: int | None,
    yolo_model_path: Path,
    tracker_backend: str,
    overlay_dir: Path | None = None,
) -> int:
    config = STORE_CONFIGS[store_id]
    layout_path = config["layout"]
    camera_list = config["cameras"]

    print(f"\n{'='*60}")
    print(f"Processing store: {store_id}")
    print(f"Video dir : {video_dir}")
    print(f"Layout    : {layout_path}")
    print(f"Cameras   : {[c['camera_id'] for c in camera_list]}")
    print(f"{'='*60}")

    per_camera_outputs: list[Path] = []

    for cam in camera_list:
        video_path = video_dir / cam["filename"]
        if not video_path.exists():
            print(f"  [SKIP] not found: {video_path}")
            continue

        camera_id = cam["camera_id"]
        out_path = out_dir / f"{camera_id}.jsonl"
        overlay_path = overlay_dir / f"{camera_id}.overlays.jsonl" if overlay_dir else None

        print(f"\n  -> {camera_id} ({cam['role']}): {video_path.name}")
        run_detection(
            video_path=video_path,
            camera_id=camera_id,
            layout_path=layout_path,
            time_offsets_path=time_offsets_path,
            output_path=out_path,
            store_id=store_id,
            session_id=session_id,
            process_fps=process_fps,
            max_seconds=max_seconds,
            min_area=min_area,
            yolo_conf=yolo_conf,
            yolo_iou=yolo_iou,
            yolo_imgsz=yolo_imgsz,
            yolo_model_path=yolo_model_path,
            overlay_path=overlay_path,
            tracker_backend=tracker_backend,
        )
        per_camera_outputs.append(out_path)

    merged_count = merge_jsonl(per_camera_outputs, merged_out)
    print(f"\nMerged {merged_count} events -> {merged_out}")
    return merged_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run detection pipeline on one or both stores."
    )
    parser.add_argument(
        "--store-id",
        choices=["STORE_BLR_002", "STORE_BLR_003", "both"],
        default="STORE_BLR_002",
        help="Which store to process. Use 'both' to process all stores.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Optional session_id attached to every generated event.",
    )
    parser.add_argument(
        "--video-dir",
        type=Path,
        default=None,
        help="Path to folder containing the .mp4 files for the selected store.",
    )
    parser.add_argument(
        "--time-offsets",
        type=Path,
        default=PROJECT_ROOT / "contracts" / "camera_time_offsets.json",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "pipeline",
    )
    parser.add_argument(
        "--merged-out",
        type=Path,
        default=PROJECT_ROOT / "data" / "events_phase5.jsonl",
    )
    parser.add_argument("--process-fps", type=float, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--min-area", type=int, default=None)
    parser.add_argument("--yolo-conf", type=float, default=None)
    parser.add_argument("--yolo-iou", type=float, default=None)
    parser.add_argument("--yolo-imgsz", type=int, default=None)
    parser.add_argument("--yolo-model-path", type=Path, default=PROJECT_ROOT / "yolov8s.pt")
    parser.add_argument("--tracker-backend", choices=["auto", "botsort", "bytetrack", "centroid"], default="auto")
    parser.add_argument(
        "--overlay-dir",
        type=Path,
        default=None,
        help="Optional directory for per-camera overlay JSONL files.",
    )
    args = parser.parse_args()

    # Default video dirs per store
    default_video_dirs = {
        "STORE_BLR_002": Path(r"D:\code\purplletech\Store 1-20260602T101818Z-3-001ec38db8\Store 1"),
        "STORE_BLR_003": Path(r"D:\code\purplletech\Store 2-20260602T101819Z-3-001099f208\Store 2"),
    }

    if args.store_id == "both":
        stores_to_run = ["STORE_BLR_002", "STORE_BLR_003"]
    else:
        stores_to_run = [args.store_id]

    total_events = 0
    for store_id in stores_to_run:
        video_dir = args.video_dir or default_video_dirs[store_id]
        out_dir = args.out_dir / store_id if args.store_id == "both" else args.out_dir
        overlay_dir = args.overlay_dir / store_id if args.overlay_dir and args.store_id == "both" else args.overlay_dir
        merged_out = (
            args.merged_out.parent / f"events_{store_id}_phase5.jsonl"
            if args.store_id == "both"
            else args.merged_out
        )
        total_events += run_store(
            store_id=store_id,
            session_id=args.session_id,
            video_dir=video_dir,
            out_dir=out_dir,
            merged_out=merged_out,
            time_offsets_path=args.time_offsets,
            process_fps=args.process_fps,
            max_seconds=args.max_seconds,
            min_area=args.min_area,
            yolo_conf=args.yolo_conf,
            yolo_iou=args.yolo_iou,
            yolo_imgsz=args.yolo_imgsz,
            yolo_model_path=args.yolo_model_path,
            tracker_backend=args.tracker_backend,
            overlay_dir=overlay_dir,
        )

    print(f"\nTotal events across all stores: {total_events}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
