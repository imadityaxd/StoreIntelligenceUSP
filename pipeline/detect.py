from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.emit import EventEmitter
from pipeline.overlay import build_overlay_frame, write_overlay_frame
from pipeline.timebase import load_time_offsets
from pipeline.tracker import CentroidTracker, Detection, ExternalDetection, ExternalIdTracker
from pipeline.tracker_profiles import apply_auto_profile
from pipeline.zones import load_layout


TRACKER_BACKENDS = {"auto", "centroid", "bytetrack", "botsort"}
EXTERNAL_TRACKER_WARMUP_SAMPLES = 5


def normalize_tracker_backend(value: str | None) -> str:
    normalized = (value or "auto").strip().lower().replace("-", "").replace("_", "")
    aliases = {
        "auto": "auto",
        "byte": "bytetrack",
        "bytetrack": "bytetrack",
        "bot": "botsort",
        "botsort": "botsort",
        "centroid": "centroid",
        "simple": "centroid",
    }
    backend = aliases.get(normalized)
    if backend not in TRACKER_BACKENDS:
        raise ValueError(f"Unsupported tracker backend: {value}. Use centroid, bytetrack, or botsort.")
    return backend


def external_tracker_assignment_decision(
    *,
    raw_detection_count: int,
    assigned_detection_count: int,
    unassigned_streak: int,
    has_assigned_ids: bool = False,
    warmup_samples: int = EXTERNAL_TRACKER_WARMUP_SAMPLES,
) -> tuple[str, int]:
    """Choose whether to keep warming up an external tracker or fall back.

    ByteTrack and BoT-SORT can legitimately return detections without IDs for
    their first few sampled frames. Falling back on the first such frame
    disables the configured tracker before it has a chance to confirm tracks.
    """
    if assigned_detection_count > 0:
        return "external", 0
    if has_assigned_ids:
        return "external", 0
    if raw_detection_count == 0:
        return "external", unassigned_streak
    next_streak = unassigned_streak + 1
    if next_streak >= max(1, int(warmup_samples)):
        return "fallback", next_streak
    return "warmup", next_streak


def run_detection(
    video_path: Path | str,
    camera_id: str,
    layout_path: Path,
    time_offsets_path: Path,
    output_path: Path,
    store_id: str = "STORE_BLR_002",
    session_id: str | None = None,
    process_fps: float | None = None,
    max_seconds: float | None = None,
    min_area: int | None = None,
    yolo_conf: float | None = None,
    yolo_iou: float | None = None,
    yolo_model_path: Path | None = None,
    yolo_imgsz: int | None = None,
    overlay_path: Path | None = None,
    tracker_backend: str = "auto",
) -> int:
    layout = load_layout(layout_path)
    cameras_by_id = {c["camera_id"]: c for c in layout.get("cameras", [])}
    camera_config = cameras_by_id.get(camera_id)
    if not camera_config:
        raise KeyError(f"Camera {camera_id} not found in layout. Available: {list(cameras_by_id)}")

    time_config = load_time_offsets(time_offsets_path)
    camera_role = camera_config.get("role", "")
    tracker_backend = normalize_tracker_backend(tracker_backend)
    resolved_profile = apply_auto_profile(
        store_id=store_id,
        camera_id=camera_id,
        role=camera_role,
        tracker_backend=tracker_backend,
        process_fps=process_fps,
        yolo_conf=yolo_conf,
        yolo_iou=yolo_iou,
        yolo_imgsz=yolo_imgsz,
        min_area=min_area,
        max_seconds=max_seconds,
    )
    process_fps = float(resolved_profile["process_fps"])
    max_seconds = float(resolved_profile["max_seconds"]) if resolved_profile["max_seconds"] is not None else None
    min_area = int(resolved_profile["min_area"])
    yolo_conf = float(resolved_profile["yolo_conf"])
    yolo_iou = float(resolved_profile["yolo_iou"])
    yolo_imgsz = int(resolved_profile["yolo_imgsz"])
    tracker_backend = str(resolved_profile["tracker_backend"])

    video_source = str(video_path)
    video_name = Path(video_source).name if "://" not in video_source else video_source.split("://", 1)[0].upper()
    capture = cv2.VideoCapture(video_source)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    frame_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    sample_every = max(1, int(round(fps / process_fps))) if fps > 0 and process_fps > 0 else 1
    max_frames = int(max_seconds * fps) if max_seconds and fps > 0 else None
    capture.release()

    min_confirmed_hits = int(resolved_profile["min_confirmed_hits"])
    zone_transition_samples = int(resolved_profile["zone_transition_samples"])
    abandon_missing_frames = 8
    if camera_role == "billing":
        abandon_missing_frames = 14

    requested_tracker_backend = normalize_tracker_backend(tracker_backend)
    active_tracker_backend = requested_tracker_backend
    centroid_tracker = CentroidTracker(max_distance=125, max_missed=18)
    external_tracker = ExternalIdTracker(max_missed=18)
    overlay_max_missed = max(3, min(centroid_tracker.max_missed, int(round(process_fps * 2.0))))
    emitter = EventEmitter(
        store_id=store_id,
        camera_id=camera_id,
        camera_config=camera_config,
        fps=fps,
        time_config=time_config,
        session_id=session_id,
        min_confirmed_hits=min_confirmed_hits,
        zone_transition_samples=zone_transition_samples,
        abandon_missing_frames=abandon_missing_frames,
        min_billing_seconds_for_abandon=2.0,
    )

    from pipeline.detect_yolo import YOLODetector
    detector = YOLODetector(
        model_size="yolov8s",
        model_path=yolo_model_path,
        conf_threshold=yolo_conf,
        iou_threshold=yolo_iou,
        device="cpu",
        imgsz=yolo_imgsz,
    )
    print(
        f"  [YOLO] model={detector.model_path.name} conf={yolo_conf} "
        f"iou={yolo_iou} imgsz={yolo_imgsz} sample_every={sample_every} "
        f"tracker={requested_tracker_backend} profile={resolved_profile['profile']['profile_name']} "
        f"confirm_hits={min_confirmed_hits} zone_samples={zone_transition_samples}"
    )

    cap = cv2.VideoCapture(video_source)
    frame_index = -1
    processed = 0
    overlay_frames = 0
    overlay_tracks = 0
    overlay_handle = None
    external_unassigned_streak = 0
    external_tracker_has_assigned_ids = False
    try:
        if overlay_path:
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
            overlay_handle = overlay_path.open("w", encoding="utf-8")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_index += 1
            if max_frames is not None and frame_index > max_frames:
                break
            if frame_index % sample_every != 0:
                continue

            raw = []
            tracker_detections: list[ExternalDetection] = []
            detections: list[Detection] = []
            if active_tracker_backend in {"bytetrack", "botsort"}:
                tracker_config = "bytetrack.yaml" if active_tracker_backend == "bytetrack" else "botsort.yaml"
                try:
                    raw = detector.track_frame(frame, tracker_config=tracker_config, persist=True)
                except Exception as exc:
                    print(f"  [WARN] {active_tracker_backend} failed, falling back to centroid: {exc}")
                    active_tracker_backend = "centroid"
                    raw = detector.detect_frame(frame)
            else:
                raw = detector.detect_frame(frame)

            for d in raw:
                x1, y1, x2, y2 = d["bbox"]
                w = x2 - x1
                h = y2 - y1
                if w * h < min_area:
                    continue
                detection = Detection(x=x1, y=y1, w=w, h=h, confidence=d["confidence"])
                tracker_id = d.get("tracker_id")
                if active_tracker_backend in {"bytetrack", "botsort"} and tracker_id is not None:
                    tracker_detections.append(ExternalDetection(track_id=int(tracker_id), detection=detection))
                else:
                    detections.append(detection)

            if active_tracker_backend in {"bytetrack", "botsort"}:
                tracker_action, external_unassigned_streak = external_tracker_assignment_decision(
                    raw_detection_count=len(raw),
                    assigned_detection_count=len(tracker_detections),
                    unassigned_streak=external_unassigned_streak,
                    has_assigned_ids=external_tracker_has_assigned_ids,
                )
                if tracker_detections:
                    external_tracker_has_assigned_ids = True
                if tracker_action == "external":
                    tracks, _ended = external_tracker.update(tracker_detections, frame_index)
                elif tracker_action == "warmup":
                    tracks, _ended = external_tracker.update([], frame_index)
                else:
                    print(
                        f"  [WARN] {active_tracker_backend} returned detections without IDs for "
                        f"{external_unassigned_streak} sampled frames; using centroid tracking"
                    )
                    active_tracker_backend = "centroid"
                    tracks, _ended = centroid_tracker.update(detections, frame_index)
            else:
                tracks, _ended = centroid_tracker.update(detections, frame_index)
            emitter.process_tracks(tracks, frame_index, frame_w, frame_h)
            if overlay_handle:
                overlay_frame = build_overlay_frame(
                    session_id=session_id,
                    camera_id=camera_id,
                    camera_config=camera_config,
                    time_config=time_config,
                    frame_index=frame_index,
                    fps=fps,
                    frame_width=frame_w,
                    frame_height=frame_h,
                    tracks=tracks,
                    visitor_id_for_track=emitter.visitor_id_for_track,
                    staff_for_track=emitter.is_staff_track,
                    validation_for_track=emitter.validation_for_track,
                    max_missed=overlay_max_missed,
                    min_confirmed_hits=min_confirmed_hits,
                )
                overlay_frames += 1
                overlay_tracks += len(overlay_frame["tracks"])
                write_overlay_frame(overlay_handle, overlay_frame)
            processed += 1
    finally:
        if overlay_handle:
            overlay_handle.close()
        cap.release()

    events = emitter.finish()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")

    print(
        f"  {camera_id}: frames_processed={processed}, events={len(events)}, "
        f"overlays={overlay_frames}/{overlay_tracks}, video={video_name}"
    )
    return len(events)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run detection pipeline for one camera.")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--layout", type=Path, default=PROJECT_ROOT / "contracts" / "store_layout.json")
    parser.add_argument("--time-offsets", type=Path, default=PROJECT_ROOT / "contracts" / "camera_time_offsets.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--store-id", default="STORE_BLR_002")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--process-fps", type=float, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--min-area", type=int, default=None)
    parser.add_argument("--yolo-conf", type=float, default=None)
    parser.add_argument("--yolo-iou", type=float, default=None)
    parser.add_argument("--yolo-imgsz", type=int, default=None)
    parser.add_argument("--yolo-model-path", type=Path, default=PROJECT_ROOT / "yolov8s.pt")
    parser.add_argument("--overlay-out", type=Path, default=None)
    parser.add_argument("--tracker-backend", choices=sorted(TRACKER_BACKENDS), default="auto")
    args = parser.parse_args()

    run_detection(
        video_path=args.video,
        camera_id=args.camera_id,
        layout_path=args.layout,
        time_offsets_path=args.time_offsets,
        output_path=args.out,
        store_id=args.store_id,
        session_id=args.session_id,
        process_fps=args.process_fps,
        max_seconds=args.max_seconds,
        min_area=args.min_area,
        yolo_conf=args.yolo_conf,
        yolo_iou=args.yolo_iou,
        yolo_model_path=args.yolo_model_path,
        yolo_imgsz=args.yolo_imgsz,
        overlay_path=args.overlay_out,
        tracker_backend=args.tracker_backend,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
