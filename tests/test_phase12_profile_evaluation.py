from __future__ import annotations

import unittest
from pathlib import Path

from scripts.evaluate_tracker_profiles import camera_jobs, recommendation_for, score_result


class Phase12ProfileEvaluationTests(unittest.TestCase):
    def test_score_rewards_assignment_and_penalizes_prediction_noise(self) -> None:
        strong = score_result(
            {
                "backend_active": "botsort",
                "frames_sampled": 10,
                "detections_total": 8,
                "assigned_ids_total": 8,
                "tracker_id_assignment_rate": 1.0,
                "avg_tracks_per_sampled_frame": 1.6,
                "unique_track_count": 3,
                "predicted_track_samples": 0,
            }
        )
        noisy = score_result(
            {
                "backend_active": "centroid",
                "frames_sampled": 10,
                "detections_total": 8,
                "assigned_ids_total": 4,
                "tracker_id_assignment_rate": 0.5,
                "avg_tracks_per_sampled_frame": 1.6,
                "unique_track_count": 6,
                "predicted_track_samples": 8,
            }
        )

        self.assertGreater(strong, noisy)

    def test_recommendation_reports_auto_match(self) -> None:
        recommendation = recommendation_for(
            [
                {
                    "backend_requested": "auto",
                    "backend_active": "botsort",
                    "frames_sampled": 5,
                    "detections_total": 5,
                    "assigned_ids_total": 5,
                    "tracker_id_assignment_rate": 1.0,
                    "avg_tracks_per_sampled_frame": 1.0,
                    "unique_track_count": 1,
                    "predicted_track_samples": 0,
                },
                {
                    "backend_requested": "centroid",
                    "backend_active": "centroid",
                    "frames_sampled": 5,
                    "detections_total": 5,
                    "assigned_ids_total": 3,
                    "tracker_id_assignment_rate": 0.6,
                    "avg_tracks_per_sampled_frame": 1.0,
                    "unique_track_count": 4,
                    "predicted_track_samples": 2,
                },
            ]
        )

        self.assertEqual(recommendation["recommended_backend"], "botsort")
        self.assertTrue(recommendation["auto_matches_recommendation"])
        self.assertGreater(recommendation["recommended_score"], 0)

    def test_camera_jobs_uses_store_camera_catalog(self) -> None:
        jobs = camera_jobs(["STORE_BLR_002"], {"STORE_BLR_002": Path("D:/missing")})
        camera_ids = {job["camera_id"] for job in jobs}

        self.assertEqual(camera_ids, {"CAM_1", "CAM_2", "CAM_3", "CAM_5"})
        self.assertTrue(all(job["store_id"] == "STORE_BLR_002" for job in jobs))

    def test_camera_jobs_supports_single_camera_filter(self) -> None:
        jobs = camera_jobs(["STORE_BLR_002"], {"STORE_BLR_002": Path("D:/missing")}, camera_id="CAM_3")

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["camera_id"], "CAM_3")
        self.assertEqual(jobs[0]["role"], "entry")


if __name__ == "__main__":
    unittest.main()
