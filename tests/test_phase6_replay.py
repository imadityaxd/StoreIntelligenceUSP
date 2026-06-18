# PROMPT: Add tests for replay batching and timing helpers in a CCTV event replay script, including timestamp ordering and chunk behavior.
# CHANGES MADE: Focused tests on pure helper functions so they run quickly without spinning up an HTTP server.

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.replay import chunked, load_events, realtime_sleep_seconds


class Phase6ReplayTests(unittest.TestCase):
    def test_chunked_batches(self) -> None:
        rows = [{"id": idx} for idx in range(7)]
        batches = chunked(rows, 3)
        self.assertEqual([len(batch) for batch in batches], [3, 3, 1])

    def test_realtime_sleep_seconds(self) -> None:
        a = [{"timestamp": "2026-04-10T14:00:00Z"}]
        b = [{"timestamp": "2026-04-10T14:00:10Z"}]
        self.assertEqual(realtime_sleep_seconds(a, b, speed=5.0), 2.0)

    def test_load_events_sorts_by_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-04-10T14:00:03Z","camera_id":"CAM_2","visitor_id":"VIS_2"}',
                        '{"timestamp":"2026-04-10T14:00:01Z","camera_id":"CAM_1","visitor_id":"VIS_1"}',
                        '{"timestamp":"2026-04-10T14:00:02Z","camera_id":"CAM_1","visitor_id":"VIS_3"}',
                    ]
                ),
                encoding="utf-8",
            )
            rows = load_events(path)
        self.assertEqual([row["timestamp"] for row in rows], [
            "2026-04-10T14:00:01Z",
            "2026-04-10T14:00:02Z",
            "2026-04-10T14:00:03Z",
        ])


if __name__ == "__main__":
    unittest.main()
