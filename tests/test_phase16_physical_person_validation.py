from __future__ import annotations

import unittest

from pipeline.emit import EventEmitter
from pipeline.overlay import build_overlay_frame
from pipeline.person_validation import validate_physical_person_track
from pipeline.tracker import Detection, Track


TIME_CONFIG = {
    "cameras": {
        "CAM_TEST": {
            "reference_local": "2026-01-01T00:00:00+00:00",
            "reference_second": 0,
        }
    }
}


def stable_person(track_id: int = 1) -> Track:
    return Track(
        track_id=track_id,
        detection=Detection(x=700, y=360, w=170, h=420, confidence=0.86),
        first_frame=0,
        last_frame=10,
        hits=7,
    )


def suspect_reflection(track_id: int = 2) -> Track:
    return Track(
        track_id=track_id,
        detection=Detection(x=1200, y=120, w=34, h=38, confidence=0.78),
        first_frame=0,
        last_frame=10,
        hits=8,
    )


class Phase16PhysicalPersonValidationTests(unittest.TestCase):
    def test_stable_full_body_track_is_countable(self) -> None:
        validation = validate_physical_person_track(
            stable_person(),
            frame_width=1920,
            frame_height=1080,
            min_confirmed_hits=3,
        )

        self.assertTrue(validation.countable)
        self.assertGreaterEqual(validation.physical_person_score, 0.42)
        self.assertIsNone(validation.ignored_reason)

    def test_tiny_upper_frame_track_is_suspect(self) -> None:
        validation = validate_physical_person_track(
            suspect_reflection(),
            frame_width=1920,
            frame_height=1080,
            min_confirmed_hits=3,
        )

        self.assertFalse(validation.countable)
        self.assertEqual(validation.ignored_reason, "REFLECTION_OR_FALSE_PERSON_SUSPECT")
        self.assertTrue(set(validation.flags) & {"bbox_too_small", "bbox_too_short_for_person"})

    def test_emitter_ignores_suspect_tracks_for_business_events(self) -> None:
        camera = {
            "camera_id": "CAM_TEST",
            "role": "zone",
            "zones_normalized": {"ZONE_MAIN": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        }
        emitter = EventEmitter(
            "STORE_TEST",
            "CAM_TEST",
            camera,
            fps=30.0,
            time_config=TIME_CONFIG,
            min_confirmed_hits=3,
        )

        emitter.process_tracks([stable_person(1), suspect_reflection(2)], frame_index=10, frame_w=1920, frame_h=1080)
        events = emitter.finish()

        self.assertEqual([event["visitor_id"] for event in events], ["VIS_CAM_TEST_00001"])
        self.assertEqual(events[0]["event_type"], "ZONE_ENTER")
        self.assertEqual(events[0]["metadata"]["person_validation"], "COUNTABLE")
        self.assertNotIn("VIS_CAM_TEST_00002", {event["visitor_id"] for event in events})

    def test_overlay_marks_suspect_tracks_without_counting_them_as_normal_people(self) -> None:
        camera = {
            "camera_id": "CAM_TEST",
            "role": "zone",
            "zones_normalized": {"ZONE_MAIN": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        }
        good = stable_person(1)
        suspect = suspect_reflection(2)
        validations = {
            1: validate_physical_person_track(good, 1920, 1080, min_confirmed_hits=3),
            2: validate_physical_person_track(suspect, 1920, 1080, min_confirmed_hits=3),
        }

        frame = build_overlay_frame(
            session_id="SESSION_PHYSICAL",
            camera_id="CAM_TEST",
            camera_config=camera,
            time_config=TIME_CONFIG,
            frame_index=10,
            fps=30.0,
            frame_width=1920,
            frame_height=1080,
            tracks=[good, suspect],
            validation_for_track=lambda track_id: validations[track_id],
        )

        by_id = {track["track_id"]: track for track in frame["tracks"]}
        self.assertTrue(by_id[1]["countable"])
        self.assertEqual(by_id[1]["person_type"], "person")
        self.assertFalse(by_id[2]["countable"])
        self.assertEqual(by_id[2]["person_type"], "suspect")
        self.assertEqual(by_id[2]["ignored_reason"], "REFLECTION_OR_FALSE_PERSON_SUSPECT")


if __name__ == "__main__":
    unittest.main()
