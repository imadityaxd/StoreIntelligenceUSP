from __future__ import annotations

import unittest

from scripts.recommend_tracker_profile_updates import apply_recommendations, effective_backend, recommendation_rows


def sample_profiles() -> dict:
    return {
        "default_profile": {"tracker_backend": "bytetrack"},
        "role_profiles": {"entry": {"tracker_backend": "bytetrack"}},
        "camera_profiles": {
            "STORE_BLR_002": {
                "CAM_3": {"profile_name": "entry", "tracker_backend": "bytetrack"}
            }
        },
    }


def sample_report(max_seconds: float = 10.0, auto_score: float = 47.0, rec_score: float = 54.0) -> dict:
    return {
        "max_seconds": max_seconds,
        "cameras": [
            {
                "store_id": "STORE_BLR_002",
                "camera_id": "CAM_3",
                "role": "entry",
                "status": "evaluated",
                "recommendation": {
                    "recommended_backend": "centroid",
                    "recommended_score": rec_score,
                    "auto_backend": "bytetrack",
                    "auto_score": auto_score,
                    "auto_matches_recommendation": False,
                    "scored_results": [
                        {
                            "backend_requested": "centroid",
                            "backend_active": "centroid",
                            "tracker_id_assignment_rate": 1.0,
                        }
                    ],
                },
            }
        ],
    }


class Phase13ProfileRecommendationTests(unittest.TestCase):
    def test_effective_backend_uses_camera_override(self) -> None:
        self.assertEqual(effective_backend(sample_profiles(), "STORE_BLR_002", "CAM_3", "entry"), "bytetrack")

    def test_short_smoke_does_not_recommend_update(self) -> None:
        rows = recommendation_rows(
            sample_report(max_seconds=1.0),
            sample_profiles(),
            min_seconds=5.0,
            min_score_margin=3.0,
            min_assignment_rate=0.75,
        )

        self.assertEqual(rows[0]["action"], "keep")
        self.assertIn("duration_below_5.0s", rows[0]["reasons"])

    def test_strong_report_recommends_backend_update(self) -> None:
        rows = recommendation_rows(
            sample_report(max_seconds=10.0, auto_score=47.0, rec_score=54.0),
            sample_profiles(),
            min_seconds=5.0,
            min_score_margin=3.0,
            min_assignment_rate=0.75,
        )

        self.assertEqual(rows[0]["action"], "update_tracker_backend")
        updated = apply_recommendations(sample_profiles(), rows)
        self.assertEqual(updated["camera_profiles"]["STORE_BLR_002"]["CAM_3"]["tracker_backend"], "centroid")

    def test_small_margin_keeps_current_profile(self) -> None:
        rows = recommendation_rows(
            sample_report(max_seconds=10.0, auto_score=52.0, rec_score=54.0),
            sample_profiles(),
            min_seconds=5.0,
            min_score_margin=3.0,
            min_assignment_rate=0.75,
        )

        self.assertEqual(rows[0]["action"], "keep")
        self.assertIn("score_margin_too_small", rows[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
