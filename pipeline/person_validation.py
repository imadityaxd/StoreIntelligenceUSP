from __future__ import annotations

from dataclasses import dataclass

from pipeline.tracker import Track


@dataclass(frozen=True)
class PhysicalPersonValidation:
    countable: bool
    physical_person_score: float
    track_quality_score: float
    footpoint_score: float
    head_evidence_score: float
    perspective_score: float
    ignored_reason: str | None
    flags: tuple[str, ...]

    def as_metadata(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "person_validation": "COUNTABLE" if self.countable else "SUSPECT",
            "physical_person_score": round(self.physical_person_score, 3),
            "track_quality_score": round(self.track_quality_score, 3),
            "footpoint_score": round(self.footpoint_score, 3),
            "head_evidence_score": round(self.head_evidence_score, 3),
            "perspective_score": round(self.perspective_score, 3),
        }
        if self.ignored_reason:
            payload["ignored_reason"] = self.ignored_reason
        if self.flags:
            payload["validation_flags"] = list(self.flags)
        return payload


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def validate_physical_person_track(
    track: Track,
    frame_width: int,
    frame_height: int,
    *,
    min_confirmed_hits: int = 3,
) -> PhysicalPersonValidation:
    """Score whether a tracked YOLO person box is a real floor person.

    This deliberately stays conservative and geometry based. YOLO says "this
    looks like a person"; this layer asks "is this a stable physical person in
    the camera scene, or a mirror/reflection/tiny partial box that should not
    become a business event?"
    """
    flags: list[str] = []
    if frame_width <= 0 or frame_height <= 0:
        return PhysicalPersonValidation(
            countable=False,
            physical_person_score=0.0,
            track_quality_score=0.0,
            footpoint_score=0.0,
            head_evidence_score=0.0,
            perspective_score=0.0,
            ignored_reason="INVALID_FRAME_GEOMETRY",
            flags=("invalid_frame_geometry",),
        )

    x1 = float(track.detection.x)
    y1 = float(track.detection.y)
    width = max(0.0, float(track.detection.w))
    height = max(0.0, float(track.detection.h))
    x2 = x1 + width
    y2 = y1 + height
    foot_x, foot_y = track.footpoint
    fw = float(frame_width)
    fh = float(frame_height)

    area_ratio = (width * height) / max(1.0, fw * fh)
    height_ratio = height / fh
    aspect = height / max(1.0, width)
    foot_x_norm = float(foot_x) / fw
    foot_y_norm = float(foot_y) / fh

    footpoint_score = 1.0
    if foot_x < -0.02 * fw or foot_x > 1.02 * fw or foot_y < -0.02 * fh or foot_y > 1.04 * fh:
        flags.append("footpoint_outside_frame")
        footpoint_score = 0.0
    elif foot_y_norm < 0.14:
        flags.append("footpoint_above_floor_band")
        footpoint_score = 0.2
    elif foot_y_norm < 0.22:
        flags.append("footpoint_near_upper_floor_band")
        footpoint_score = 0.65

    if x2 < 0 or x1 > fw or y2 < 0 or y1 > fh:
        flags.append("bbox_outside_frame")
    if area_ratio < 0.0012:
        flags.append("bbox_too_small")
    if area_ratio > 0.45:
        flags.append("bbox_too_large")
    if aspect < 0.95:
        flags.append("bbox_too_flat")
    if height_ratio < 0.06:
        flags.append("bbox_too_short_for_person")

    hit_score = _clamp01(track.hits / max(1.0, float(min_confirmed_hits + 3)))
    confidence_score = _clamp01((float(track.detection.confidence) - 0.20) / 0.65)
    missed_score = _clamp01(1.0 - (track.missed / 8.0))
    track_quality_score = (hit_score * 0.45) + (confidence_score * 0.40) + (missed_score * 0.15)

    # Geometry-only proxy: a real full-body/upper-body person box should have
    # enough vertical extent above the footpoint. This is not a face detector;
    # it is a cheap guard against reflection slivers and product/poster boxes.
    head_evidence_score = _clamp01((height_ratio - 0.055) / 0.22)
    if head_evidence_score < 0.25:
        flags.append("weak_head_upper_body_evidence")

    # In fixed retail CCTV, people lower in the image normally appear larger.
    # Keep this loose because cameras are wide angle and mounted differently.
    expected_height = 0.08 + (0.30 * _clamp01(foot_y_norm))
    perspective_error = abs(height_ratio - expected_height) / max(0.12, expected_height)
    perspective_score = _clamp01(1.0 - perspective_error)
    if perspective_score < 0.18:
        flags.append("perspective_size_mismatch")

    if area_ratio < 0.0012 or aspect < 0.95 or height_ratio < 0.06:
        shape_score = 0.0
    else:
        shape_score = 1.0

    physical_person_score = (
        footpoint_score * 0.30
        + track_quality_score * 0.30
        + head_evidence_score * 0.18
        + perspective_score * 0.12
        + shape_score * 0.10
    )

    hard_flags = {
        "footpoint_outside_frame",
        "bbox_outside_frame",
        "bbox_too_small",
        "bbox_too_flat",
        "bbox_too_short_for_person",
    }
    countable = (
        track.hits >= min_confirmed_hits
        and not any(flag in hard_flags for flag in flags)
        and physical_person_score >= 0.42
    )
    ignored_reason = None if countable else "REFLECTION_OR_FALSE_PERSON_SUSPECT"

    return PhysicalPersonValidation(
        countable=countable,
        physical_person_score=round(physical_person_score, 6),
        track_quality_score=round(track_quality_score, 6),
        footpoint_score=round(footpoint_score, 6),
        head_evidence_score=round(head_evidence_score, 6),
        perspective_score=round(perspective_score, 6),
        ignored_reason=ignored_reason,
        flags=tuple(flags),
    )
