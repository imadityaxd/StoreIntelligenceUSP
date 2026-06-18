from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pipeline.person_validation import PhysicalPersonValidation, validate_physical_person_track
from pipeline.timebase import frame_timestamp_iso
from pipeline.tracker import Track
from pipeline.zones import zones_for_point
import numpy as np


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class TrackSessionState:
    visitor_id: str
    sequence: int = 0
    current_zone: str | None = None
    zone_enter_frame: int | None = None
    last_line_side: float | None = None
    saw_entry: bool = False
    saw_exit: bool = False
    missing_frames: int = 0
    billing_enter_frame: int | None = None
    abandon_emitted_for_segment: bool = False
    pending_dwell_milestones: set[int] | None = None
    frames_in_billing: int = 0
    frames_total: int = 0
    is_staff_override: bool = False
    physical_person_score: float = 1.0
    track_quality_score: float = 1.0
    person_validation: str = "COUNTABLE"
    ignored_reason: str | None = None
    validation_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.pending_dwell_milestones is None:
            self.pending_dwell_milestones = set()


class EventEmitter:
    def __init__(
        self,
        store_id: str,
        camera_id: str,
        camera_config: dict[str, Any],
        fps: float,
        time_config: dict[str, Any],
        session_id: str | None = None,
        min_confirmed_hits: int = 3,
        abandon_missing_frames: int = 8,
        min_billing_seconds_for_abandon: float = 1.5,
    ) -> None:
        self.store_id = store_id
        self.session_id = session_id
        self.camera_id = camera_id
        self.camera_config = camera_config
        self.fps = fps
        self.time_config = time_config
        self.min_confirmed_hits = min_confirmed_hits
        self.abandon_missing_frames = abandon_missing_frames
        self.min_billing_seconds_for_abandon = min_billing_seconds_for_abandon
        self.states: dict[int, TrackSessionState] = {}
        self.events: list[dict[str, Any]] = []
        self.track_validations: dict[int, PhysicalPersonValidation] = {}

    def _next_seq(self, state: TrackSessionState) -> int:
        state.sequence += 1
        return state.sequence

    def visitor_id_for_track(self, track_id: int) -> str:
        state = self.states.get(track_id)
        return state.visitor_id if state else f"VIS_{self.camera_id}_{track_id:05d}"

    def is_staff_track(self, track_id: int, zone_id: str | None) -> bool:
        return self._is_staff(track_zone=zone_id, state=self.states.get(track_id))

    def validation_for_track(self, track_id: int) -> PhysicalPersonValidation | None:
        return self.track_validations.get(track_id)

    def is_countable_track(self, track_id: int) -> bool:
        validation = self.track_validations.get(track_id)
        return True if validation is None else validation.countable

    def _apply_validation(self, state: TrackSessionState, validation: PhysicalPersonValidation) -> None:
        state.physical_person_score = validation.physical_person_score
        state.track_quality_score = validation.track_quality_score
        state.person_validation = "COUNTABLE" if validation.countable else "SUSPECT"
        state.ignored_reason = validation.ignored_reason
        state.validation_flags = validation.flags

    def _emit(
        self,
        state: TrackSessionState,
        event_type: str,
        frame_index: int,
        zone_id: str | None,
        dwell_ms: int,
        confidence: float,
        queue_depth: int | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata = {
            "queue_depth": queue_depth,
            "sku_zone": "billing" if zone_id == "BILLING_COUNTER" else None,
            "session_seq": self._next_seq(state),
            "person_validation": state.person_validation,
            "physical_person_score": round(float(state.physical_person_score), 3),
            "track_quality_score": round(float(state.track_quality_score), 3),
        }
        if state.ignored_reason:
            metadata["ignored_reason"] = state.ignored_reason
        if state.validation_flags:
            metadata["validation_flags"] = list(state.validation_flags)
        if extra_metadata:
            metadata.update(extra_metadata)
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": state.visitor_id,
            "event_type": event_type,
            "timestamp": frame_timestamp_iso(self.camera_id, frame_index, self.fps, self.time_config),
            "zone_id": zone_id,
            "dwell_ms": dwell_ms,
            "is_staff": self._is_staff(track_zone=zone_id, state=state),
            "confidence": round(float(confidence), 3),
            "metadata": metadata,
        }
        if self.session_id:
            event["session_id"] = self.session_id
        self.events.append(event)

    def _is_staff(self, track_zone: str | None, state: "TrackSessionState | None" = None) -> bool:
        role = self.camera_config.get("role", "")
        if role in {"stock_or_staff_area", "staff-area", "staff_area"}:
            return True
        if role in {"billing_counter", "billing"} and track_zone == "BACKLIT":
            return True
        # Persistent billing presence = staff (never leaves counter)
        if state is not None and state.is_staff_override:
            return True
        if state is not None and role in {"billing", "billing_counter"} and state.frames_total >= 10:
            billing_ratio = state.frames_in_billing / state.frames_total
            if billing_ratio >= 0.90:
                return True
        return False
    
    @staticmethod
    def _is_dark_clothing(frame: Any, track: Track, dark_threshold: float = 0.25) -> bool:
        """Return True if the person's clothing is predominantly dark/black."""
        try:
            import cv2
            x1 = int(track.x)
            y1 = int(track.y)
            x2 = int(track.x + track.w)
            y2 = int(track.y + track.h)
            # Crop torso only (middle third vertically) to avoid head/floor
            torso_y1 = y1 + (y2 - y1) // 3
            torso_y2 = y1 + 2 * (y2 - y1) // 3
            if torso_y2 <= torso_y1 or x2 <= x1:
                return False
            crop = frame[torso_y1:torso_y2, x1:x2]
            if crop.size == 0:
                return False
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            # V channel: 0=black, 255=white
            mean_v = hsv[:, :, 2].mean() / 255.0
            return mean_v < dark_threshold
        except Exception:
            return False

    def _line_side(self, point: tuple[int, int], frame_width: int, frame_height: int) -> float | None:
        line = self.camera_config.get("entry_line_normalized")
        if not line:
            return None
        (x1n, y1n), (x2n, y2n) = line
        x1 = x1n * frame_width
        y1 = y1n * frame_height
        x2 = x2n * frame_width
        y2 = y2n * frame_height
        px, py = point
        return (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)

    def _handle_entry_exit(self, state: TrackSessionState, track: Track, frame_index: int, frame_w: int, frame_h: int) -> None:
        side = self._line_side(track.footpoint, frame_w, frame_h)
        if side is None:
            return
        if state.last_line_side is None:
            state.last_line_side = side
            return

        direction_hint = self.camera_config.get("entry_direction", "")
        epsilon = 8.0
        crossed = (side < -epsilon <= state.last_line_side) or (side > epsilon >= state.last_line_side)
        if not crossed:
            state.last_line_side = side
            return

        inside_cross = side < state.last_line_side
        if "right_to_left" in direction_hint:
            inside_cross = track.footpoint[0] < (self.camera_config["entry_line_normalized"][0][0] * frame_w)

        if inside_cross:
            event_type = "REENTRY" if state.saw_exit else "ENTRY"
            self._emit(state, event_type, frame_index, None, 0, track.detection.confidence)
            state.saw_entry = True
        else:
            self._emit(state, "EXIT", frame_index, None, 0, track.detection.confidence)
            state.saw_exit = True
        state.last_line_side = side

    def _handle_zone_transitions(
        self,
        state: TrackSessionState,
        track: Track,
        frame_index: int,
        target_zone: str | None,
        billing_queue_depth: int,
    ) -> None:

        if target_zone != state.current_zone:
            entry_camera = self.camera_config.get("role") in {"entry_exit", "entry"}
            previous_zone = state.current_zone
            emit_zone_exit = previous_zone is not None and not (entry_camera and previous_zone == "ENTRY_EXIT")
            emit_zone_enter = target_zone is not None and not (entry_camera and target_zone == "ENTRY_EXIT")

            if emit_zone_exit:
                self._emit(
                    state,
                    "ZONE_EXIT",
                    frame_index,
                    previous_zone,
                    0,
                    track.detection.confidence,
                )

            if emit_zone_enter:
                event_type = "BILLING_QUEUE_JOIN" if target_zone == "BILLING_COUNTER" else "ZONE_ENTER"
                queue_depth = billing_queue_depth if target_zone == "BILLING_COUNTER" else None
                self._emit(
                    state,
                    event_type,
                    frame_index,
                    target_zone,
                    0,
                    track.detection.confidence,
                    queue_depth=queue_depth,
                )
                state.zone_enter_frame = frame_index
                state.pending_dwell_milestones = set()
                if target_zone == "BILLING_COUNTER":
                    state.billing_enter_frame = frame_index
                    state.abandon_emitted_for_segment = False

            if entry_camera and previous_zone is None and target_zone == "ENTRY_EXIT":
                fallback_event_type = "REENTRY" if state.saw_exit else "ENTRY"
                self._emit(
                    state,
                    fallback_event_type,
                    frame_index,
                    None,
                    0,
                    track.detection.confidence,
                    extra_metadata={"fallback_rule": "entry_zone_enter"},
                )
                state.saw_entry = True

            if target_zone is None and entry_camera and previous_zone in {"ENTRY_EXIT", None} and state.saw_entry:
                # Fallback close-session signal when line-crossing is missed but track clearly leaves the threshold zone.
                self._emit(
                    state,
                    "EXIT",
                    frame_index,
                    None,
                    0,
                    track.detection.confidence,
                    extra_metadata={"fallback_rule": "entry_zone_exit"},
                )
                state.saw_exit = True

            if target_zone is None:
                state.zone_enter_frame = None
                state.pending_dwell_milestones = set()
                state.billing_enter_frame = None
            elif target_zone != "BILLING_COUNTER":
                state.billing_enter_frame = None
            state.current_zone = target_zone
            return

        if target_zone and state.zone_enter_frame is not None:
            elapsed_seconds = (frame_index - state.zone_enter_frame) / self.fps if self.fps > 0 else 0.0
            for milestone_sec in (30, 60, 90):
                if elapsed_seconds >= milestone_sec and milestone_sec not in state.pending_dwell_milestones:
                    self._emit(
                        state,
                        "ZONE_DWELL",
                        frame_index,
                        target_zone,
                        milestone_sec * 1000,
                        track.detection.confidence,
                    )
                    state.pending_dwell_milestones.add(milestone_sec)

    def process_tracks(self, tracks: list[Track], frame_index: int, frame_w: int, frame_h: int) -> None:
        visible_tracks = [track for track in tracks if track.missed == 0 and track.hits >= self.min_confirmed_hits]
        validations = {
            track.track_id: validate_physical_person_track(
                track,
                frame_w,
                frame_h,
                min_confirmed_hits=self.min_confirmed_hits,
            )
            for track in visible_tracks
        }
        self.track_validations.update(validations)
        countable_tracks = [track for track in visible_tracks if validations[track.track_id].countable]
        visible_ids = {track.track_id for track in countable_tracks}
        track_zone_map: dict[int, str | None] = {}
        for track in countable_tracks:
            zones = zones_for_point(
                self.camera_config,
                track.footpoint,
                frame_w,
                frame_h,
            )
            track_zone_map[track.track_id] = zones[0] if zones else None
        billing_queue_depth = sum(1 for zone in track_zone_map.values() if zone == "BILLING_COUNTER")

        for track in countable_tracks:
            state = self.states.get(track.track_id)
            if state is None:
                state = TrackSessionState(visitor_id=f"VIS_{self.camera_id}_{track.track_id:05d}")
                self.states[track.track_id] = state
            self._apply_validation(state, validations[track.track_id])
            state.missing_frames = 0
            state.frames_total += 1
            current_zone = track_zone_map.get(track.track_id)
            if current_zone == "BILLING_COUNTER":
                state.frames_in_billing += 1

            self._handle_entry_exit(state, track, frame_index, frame_w, frame_h)
            self._handle_zone_transitions(
                state,
                track,
                frame_index,
                track_zone_map.get(track.track_id),
                billing_queue_depth,
            )

        # Mark potential abandon only after sustained disappearance from billing.
        for track_id, state in list(self.states.items()):
            if track_id not in visible_ids:
                state.missing_frames += 1
                # Confirm track dead after max_missed threshold and emit EXIT
                if state.missing_frames == self.abandon_missing_frames and state.saw_entry and not state.saw_exit:
                    self.close_track(track_id, frame_index)
            if (
                track_id not in visible_ids
                and state.current_zone == "BILLING_COUNTER"
                and not state.abandon_emitted_for_segment
                and state.missing_frames >= self.abandon_missing_frames
            ):
                dwell_seconds = 0.0
                if state.billing_enter_frame is not None and self.fps > 0:
                    dwell_seconds = (frame_index - state.billing_enter_frame) / self.fps
                if dwell_seconds >= self.min_billing_seconds_for_abandon:
                    self._emit(
                        state,
                        "BILLING_QUEUE_ABANDON",
                        frame_index,
                        "BILLING_COUNTER",
                        0,
                        0.5,
                    )
                state.abandon_emitted_for_segment = True
                state.current_zone = None
    def close_track(self, track_id: int, frame_index: int) -> None:
        """Called when a track is confirmed ended — emit EXIT if needed."""
        state = self.states.get(track_id)
        if state is None:
            return
        if state.saw_entry and not state.saw_exit:
            self._emit(
                state,
                "EXIT",
                frame_index,
                None,
                0,
                0.5,
                extra_metadata={"fallback_rule": "track_ended"},
            )
            state.saw_exit = True
        # Also close any open zone
        if state.current_zone is not None:
            self._emit(
                state,
                "ZONE_EXIT",
                frame_index,
                state.current_zone,
                0,
                0.5,
                extra_metadata={"fallback_rule": "track_ended"},
            )
            state.current_zone = None

    def finish(self) -> list[dict[str, Any]]:
        return sorted(
            self.events,
            key=lambda row: (row["timestamp"], row["camera_id"], row["visitor_id"], row["metadata"]["session_seq"]),
        )
