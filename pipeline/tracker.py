from __future__ import annotations

from dataclasses import dataclass
from math import hypot


@dataclass
class Detection:
    x: int
    y: int
    w: int
    h: int
    confidence: float

    @property
    def centroid(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def footpoint(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h)

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass
class ExternalDetection:
    track_id: int
    detection: Detection


@dataclass
class Track:
    track_id: int
    detection: Detection
    first_frame: int
    last_frame: int
    missed: int = 0
    hits: int = 1
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    last_observed_frame: int | None = None

    def __post_init__(self) -> None:
        if self.last_observed_frame is None:
            self.last_observed_frame = self.last_frame

    @property
    def centroid(self) -> tuple[int, int]:
        return self.detection.centroid

    @property
    def footpoint(self) -> tuple[int, int]:
        return self.detection.footpoint

    def predicted_detection(self, frame_index: int) -> Detection:
        frame_delta = max(0, frame_index - self.last_frame)
        if frame_delta == 0 or (self.velocity_x == 0.0 and self.velocity_y == 0.0):
            return self.detection
        return Detection(
            x=int(round(self.detection.x + self.velocity_x * frame_delta)),
            y=int(round(self.detection.y + self.velocity_y * frame_delta)),
            w=self.detection.w,
            h=self.detection.h,
            confidence=self.detection.confidence,
        )


class CentroidTracker:
    def __init__(
        self,
        max_distance: float = 130.0,
        max_missed: int = 20,
        iou_match_threshold: float = 0.04,
    ) -> None:
        self.max_distance = max_distance
        self.max_missed = max_missed
        self.iou_match_threshold = iou_match_threshold
        self.next_track_id = 1
        self.tracks: dict[int, Track] = {}

    @staticmethod
    def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        intersection_w = max(0, ix2 - ix1)
        intersection_h = max(0, iy2 - iy1)
        intersection = intersection_w * intersection_h
        if intersection <= 0:
            return 0.0
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - intersection
        return intersection / union if union > 0 else 0.0

    def _new_track(self, detection: Detection, frame_index: int) -> Track:
        track = Track(
            track_id=self.next_track_id,
            detection=detection,
            first_frame=frame_index,
            last_frame=frame_index,
            missed=0,
            hits=1,
        )
        self.tracks[self.next_track_id] = track
        self.next_track_id += 1
        return track

    def _mark_missed(self, track: Track, frame_index: int) -> None:
        predicted = track.predicted_detection(frame_index)
        track.detection = Detection(
            x=predicted.x,
            y=predicted.y,
            w=predicted.w,
            h=predicted.h,
            confidence=max(0.05, min(predicted.confidence, track.detection.confidence * 0.92)),
        )
        track.last_frame = frame_index
        track.missed += 1

    def _match_track(self, track: Track, detection: Detection, frame_index: int) -> None:
        predicted = track.predicted_detection(frame_index)
        px, py = predicted.centroid
        dx, dy = detection.centroid
        frame_delta = max(1, frame_index - track.last_frame)
        measured_vx = (dx - px) / frame_delta
        measured_vy = (dy - py) / frame_delta
        if track.hits <= 1:
            track.velocity_x = measured_vx
            track.velocity_y = measured_vy
        else:
            track.velocity_x = (track.velocity_x * 0.65) + (measured_vx * 0.35)
            track.velocity_y = (track.velocity_y * 0.65) + (measured_vy * 0.35)
        track.detection = detection
        track.last_frame = frame_index
        track.last_observed_frame = frame_index
        track.missed = 0
        track.hits += 1

    def update(self, detections: list[Detection], frame_index: int) -> tuple[list[Track], list[Track]]:
        ended: list[Track] = []

        if not self.tracks:
            for detection in detections:
                self._new_track(detection, frame_index)
            return list(self.tracks.values()), ended

        if not detections:
            for track in list(self.tracks.values()):
                self._mark_missed(track, frame_index)
                if track.missed > self.max_missed:
                    ended.append(self.tracks.pop(track.track_id))
            return list(self.tracks.values()), ended

        candidates: list[tuple[float, int, int]] = []
        track_ids = list(self.tracks.keys())
        for track_id in track_ids:
            track = self.tracks[track_id]
            predicted = track.predicted_detection(frame_index)
            tx, ty = predicted.centroid
            for d_idx, detection in enumerate(detections):
                dx, dy = detection.centroid
                distance = hypot(dx - tx, dy - ty)
                iou = self._bbox_iou(predicted.bbox, detection.bbox)
                adaptive_distance = max(
                    self.max_distance,
                    min(260.0, max(predicted.w, predicted.h) * 0.45),
                )
                if distance <= adaptive_distance or iou >= self.iou_match_threshold:
                    score = distance - (iou * 80.0)
                    candidates.append((score, track_id, d_idx))
        candidates.sort(key=lambda item: item[0])

        assigned_tracks: set[int] = set()
        assigned_detections: set[int] = set()

        for _score, track_id, d_idx in candidates:
            if track_id in assigned_tracks or d_idx in assigned_detections:
                continue
            track = self.tracks[track_id]
            self._match_track(track, detections[d_idx], frame_index)
            assigned_tracks.add(track_id)
            assigned_detections.add(d_idx)

        for track_id, track in list(self.tracks.items()):
            if track_id not in assigned_tracks:
                self._mark_missed(track, frame_index)
                if track.missed > self.max_missed:
                    ended.append(self.tracks.pop(track_id))

        for d_idx, detection in enumerate(detections):
            if d_idx not in assigned_detections:
                self._new_track(detection, frame_index)

        return list(self.tracks.values()), ended


class ExternalIdTracker:
    """Track manager for detector-supplied IDs such as ByteTrack/BoT-SORT."""

    def __init__(self, max_missed: int = 20) -> None:
        self.max_missed = max_missed
        self.tracks: dict[int, Track] = {}

    @staticmethod
    def _match_track(track: Track, detection: Detection, frame_index: int) -> None:
        predicted = track.predicted_detection(frame_index)
        px, py = predicted.centroid
        dx, dy = detection.centroid
        frame_delta = max(1, frame_index - track.last_frame)
        measured_vx = (dx - px) / frame_delta
        measured_vy = (dy - py) / frame_delta
        if track.hits <= 1:
            track.velocity_x = measured_vx
            track.velocity_y = measured_vy
        else:
            track.velocity_x = (track.velocity_x * 0.65) + (measured_vx * 0.35)
            track.velocity_y = (track.velocity_y * 0.65) + (measured_vy * 0.35)
        track.detection = detection
        track.last_frame = frame_index
        track.last_observed_frame = frame_index
        track.missed = 0
        track.hits += 1

    @staticmethod
    def _mark_missed(track: Track, frame_index: int) -> None:
        predicted = track.predicted_detection(frame_index)
        track.detection = Detection(
            x=predicted.x,
            y=predicted.y,
            w=predicted.w,
            h=predicted.h,
            confidence=max(0.05, min(predicted.confidence, track.detection.confidence * 0.92)),
        )
        track.last_frame = frame_index
        track.missed += 1

    def update(self, detections: list[ExternalDetection], frame_index: int) -> tuple[list[Track], list[Track]]:
        ended: list[Track] = []
        observed_ids: set[int] = set()

        for row in detections:
            observed_ids.add(row.track_id)
            track = self.tracks.get(row.track_id)
            if track is None:
                self.tracks[row.track_id] = Track(
                    track_id=row.track_id,
                    detection=row.detection,
                    first_frame=frame_index,
                    last_frame=frame_index,
                    missed=0,
                    hits=1,
                    last_observed_frame=frame_index,
                )
            else:
                self._match_track(track, row.detection, frame_index)

        for track_id, track in list(self.tracks.items()):
            if track_id in observed_ids:
                continue
            self._mark_missed(track, frame_index)
            if track.missed > self.max_missed:
                ended.append(self.tracks.pop(track_id))

        return list(self.tracks.values()), ended
