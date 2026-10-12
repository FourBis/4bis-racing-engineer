import unittest
from racing_engineer.model import Sample
from racing_engineer.review import G_MPS2, review_vertical, validate_marker


def sample(time_s, *, session="s1", lap=1, progress=0.5, g=1.0,
           on_track=True, replay=False, speed=20.0):
    return Sample(
        source="demo", session_id=session, time_s=time_s, lap=lap,
        lap_dist_pct=progress, speed_mps=speed, on_track=on_track,
        is_replay=replay, vert_accel_mps2=None if g is None else g * G_MPS2,
        throttle_raw=0.4, brake_raw=0.1,
    )


class ReviewTests(unittest.TestCase):
    def test_filters_states_and_splits_clusters_at_invalid_samples_and_gaps(self):
        rows = [
            sample(0.0, g=2.1), sample(0.1, g=2.2),
            sample(0.2, g=None), sample(0.3, g=2.3),
            sample(0.4, g=2.4, on_track=False), sample(0.5, g=2.5),
            sample(0.9, g=2.6), sample(1.0, g=2.7, replay=True),
            sample(1.1, g=2.8, speed=4.9),
        ]
        result = review_vertical(rows, [])
        self.assertEqual(len(result["events"]), 4)
        self.assertEqual([event["time_s"] for event in result["events"]], [0.1, 0.3, 0.5, 0.9])
        self.assertTrue(result["available"])

    def test_uses_only_last_session_and_peak_sample_with_manual_circular_match(self):
        rows = [sample(0.0, session="old", g=3.0),
                sample(1.0, session="new", progress=0.999, g=2.1),
                sample(1.4, session="new", progress=0.001, g=0.2),
                sample(1.8, session="new", progress=0.5, g=2.4)]
        markers = [
            {"name": "5", "session_id": "new", "progress_pct": 0.999},
            {"name": "10b", "session_id": "new", "progress_pct": 0.001},
            {"name": "11", "session_id": "old", "progress_pct": 0.5},
        ]
        result = review_vertical(rows, markers)
        self.assertEqual(result["session_id"], "new")
        self.assertEqual(len(result["markers"]), 2)
        self.assertEqual(len(result["events"]), 3)
        self.assertEqual(result["events"][0]["corner"], "5")
        self.assertAlmostEqual(result["events"][0]["vert_g"], 2.1)
        self.assertEqual(result["events"][1]["corner"], "10b")
        self.assertAlmostEqual(result["events"][1]["vert_g"], 0.2)
        self.assertEqual(result["events"][2]["corner"], None)
        corner = next(row for row in result["by_corner"] if row["name"] == "10b")
        self.assertEqual((corner["events"], corner["laps"]), (1, 1))
        self.assertAlmostEqual(corner["min_g"], 0.2)

    def test_marker_validation_rejects_unknown_keys_bool_and_invalid_numbers(self):
        marker = {"name": "5", "session_id": "s1", "progress_pct": 0.5}
        self.assertEqual(validate_marker(marker), marker)
        for invalid in (
            {**marker, "csrf": "ignored"},
            {**marker, "progress_pct": True},
            {**marker, "progress_pct": float("nan")},
            {**marker, "progress_pct": 1.01},
            {**marker, "name": "T1"},
            {**marker, "session_id": ""},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_marker(invalid)

    def test_equal_distance_overlap_is_deterministic_and_missing_progress_unmapped(self):
        rows = [sample(0.0, progress=0.5, g=2.2),
                sample(0.5, progress=None, g=2.3)]
        result = review_vertical(rows, [
            {"name": "11", "session_id": "s1", "progress_pct": 0.49},
            {"name": "10b", "session_id": "s1", "progress_pct": 0.51},
        ])
        self.assertEqual(result["events"][0]["corner"], "10b")
        self.assertIsNone(result["events"][1]["corner"])


if __name__ == "__main__":
    unittest.main()
