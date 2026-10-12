import unittest

from racing_engineer.corners import _ROAD_ATLANTA
from racing_engineer.track_map import ROAD_ATLANTA_MAP


class TrackMapTests(unittest.TestCase):
    def test_road_atlanta_centerline_is_closed_oriented_and_matches_corner_anchors(self):
        track = ROAD_ATLANTA_MAP
        self.assertEqual((127, "Full Course"), (track["track_id"], track["layout"]))
        self.assertEqual([0, 0, 1000, 650], track["view_box"])
        self.assertEqual(201, len(track["points"]))
        self.assertEqual([0, *track["points"][0][1:]], track["points"][0])
        self.assertEqual(track["points"][0][1:], track["points"][-1][1:])
        self.assertEqual([i / 2 for i in range(201)], [row[0] for row in track["points"]])
        self.assertTrue(all(0 <= x <= 1000 and 0 <= y <= 650 for _, x, y in track["points"]))

        labels = track["labels"]
        self.assertEqual([name for name, _ in _ROAD_ATLANTA], [row["name"] for row in labels])
        self.assertTrue(all(0 <= row["x"] <= 1000 and 0 <= row["y"] <= 650 for row in labels))
        for (name, progress), label in zip(_ROAD_ATLANTA, labels):
            self.assertEqual(name, label["name"])
            self.assertAlmostEqual(progress * 100, label["progress_pct"], places=3)
        self.assertIn("no es una trayectoria real", track["note"])


if __name__ == "__main__":
    unittest.main()
