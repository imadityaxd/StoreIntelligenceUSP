"""
detect_yolo.py - YOLOv8-based person detector for the USP pipeline.

Drop-in replacement for detect.py. Outputs the same per-frame
detection list: [{"bbox": [x1,y1,x2,y2], "confidence": float, "track_id": int}]

PROMPT BLOCK
# PROMPT: Asked Claude to design a YOLOv8 person-only detector that:
#   - runs on CPU without GPU requirement
#   - outputs confidence scores rather than suppressing low-conf detections
#   - integrates with the existing tracker.py centroid tracker
#   - handles the anonymised (face-blurred) footage correctly
# CHANGES MADE:
#   - Added conf_threshold as a tunable param (challenge wants graceful degradation not hard drop)
#   - Added iou_threshold param for NMS tuning on crowded billing scenes
#   - Prefer local model weights so USP runs do not auto-download.
"""

from __future__ import annotations
from pathlib import Path
from typing import Any
import cv2
import numpy as np

try:
    from ultralytics import YOLO
except ImportError:
    raise ImportError("Run: pip install ultralytics")


class YOLODetector:
    """
    Wraps YOLOv8 small by default for person detection on retail CCTV.

    Key tunable parameters (see PARAMETERS section below):
        conf_threshold  - lower = more detections, more noise
        iou_threshold   - lower = fewer overlapping boxes (important for groups)
        model_size      - n/s/m/l/x tradeoff speed vs accuracy
    """

    # -- PARAMETERS TO TUNE ---------------------------------------------------
    # These are the levers that affect your Part A score most directly.
    #
    # conf_threshold:
    #   0.25 = aggressive (catches partial occlusions, more false positives)
    #   0.45 = balanced  <- start here
    #   0.65 = conservative (clean but misses occluded people)
    #
    # iou_threshold (NMS overlap threshold):
    #   0.35 = splits group entries into individuals <- important for group handling
    #   0.50 = standard
    #   0.70 = allows more overlap (merges nearby people -> bad for group counting)
    #
    # model_size:
    #   "yolov8n" = nano, fastest, CPU-friendly, least accurate
    #   "yolov8s" = small <- best CPU tradeoff for 1080p CCTV
    #   "yolov8m" = medium, needs ~8GB RAM, slower on CPU
    # -------------------------------------------------------------------------

    def __init__(
        self,
        model_size: str = "yolov8s",
        model_path: str | Path | None = None,
        conf_threshold: float = 0.45,
        iou_threshold: float = 0.35,
        device: str = "cpu",
        imgsz: int = 960,
    ):
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.device = device
        self.imgsz = imgsz
        project_root = Path(__file__).resolve().parents[1]
        resolved_model_path = Path(model_path) if model_path else project_root / f"{model_size}.pt"
        if not resolved_model_path.exists():
            raise FileNotFoundError(
                f"YOLO weights not found: {resolved_model_path}. "
                "Place the model file in the project root or pass --yolo-model-path."
            )
        self.model_path = resolved_model_path
        self.model = YOLO(str(resolved_model_path))

    def detect_frame(self, frame: np.ndarray) -> list[dict[str, Any]]:
        """
        Run inference on one frame.
        Returns list of dicts: {bbox, confidence, class_id}
        Only returns class_id=0 (person).
        """
        results = self.model(
            frame,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            imgsz=self.imgsz,
            classes=[0],          # person class only
            device=self.device,
            verbose=False,
        )

        detections = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                detections.append({
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "confidence": round(conf, 4),
                    "class_id": int(box.cls[0]),
                    "center": (int((x1 + x2) / 2), int((y1 + y2) / 2)),
                    "area": int((x2 - x1) * (y2 - y1)),
                })
        return detections

    def track_frame(
        self,
        frame: np.ndarray,
        tracker_config: str = "bytetrack.yaml",
        persist: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Run Ultralytics tracking on one sampled frame.

        Returns the same detection fields as detect_frame plus tracker_id when
        the underlying tracker assigns one. The caller can fall back to centroid
        tracking if tracker_id is missing.
        """
        results = self.model.track(
            frame,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            imgsz=self.imgsz,
            classes=[0],
            device=self.device,
            tracker=tracker_config,
            persist=persist,
            verbose=False,
        )

        detections = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                tracker_id = None
                if box.id is not None:
                    tracker_id = int(box.id[0])
                detections.append({
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "confidence": round(conf, 4),
                    "class_id": int(box.cls[0]),
                    "tracker_id": tracker_id,
                    "center": (int((x1 + x2) / 2), int((y1 + y2) / 2)),
                    "area": int((x2 - x1) * (y2 - y1)),
                })
        return detections

    def detect_video(
        self,
        video_path: str | Path,
        frame_skip: int = 3,
    ):
        """
        Generator: yields (frame_idx, frame, detections) for each sampled frame.

        frame_skip=3 means process every 3rd frame (5fps from 15fps source).
        Lower = more accurate tracking, slower processing.
        Higher = faster, may miss fast movements.
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        frame_idx = 0
        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_idx % frame_skip == 0:
                    detections = self.detect_frame(frame)
                    yield frame_idx, frame, detections
                frame_idx += 1
        finally:
            cap.release()
