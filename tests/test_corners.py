import unittest

from racing_engineer.corners import corner_guidance


def guide(progress, **track_changes):
    point = {"id": 1, "lap_dist_pct": progress, "initial_brake_class": "fuerte",
             "reference_brake_peak": 1.0, "seconds_ahead": 5}
    return {"track": {"track_id": 127, "layout": "Full Course", "length_m": 4056.9,
                      **track_changes}, "zones": [point], "next": dict(point), "voice_ready": True}


class CornerTests(unittest.TestCase):
    def test_known_approaches_use_real_corner_names_and_distinguish_six_from_seven(self):
        for progress, name in ((.067545, "1"), (.3207, "5"), (.449485, "6"),
                               (.5051, "7"), (.818324, "10a")):
            result = corner_guidance(guide(progress))
            point = result["next"]
            self.assertEqual(f"Curva {name}", point["corner_name"])
            self.assertEqual(point, result["zones"][0])
            self.assertTrue(result["voice_ready"])
            self.assertIn("Freno fuerte y suelta progresivo", point["cue"])
            self.assertNotIn("100", point["cue"])
            self.assertFalse(point["target_validated"])
            self.assertEqual("practice_proposal", point["instruction_basis"])

    def test_unknown_ambiguous_and_outside_approach_positions_have_no_spoken_fallback(self):
        for progress in (0, .1744, .206, .488, .518, .85, .9, .99):
            result = corner_guidance(guide(progress))
            self.assertIsNone(result["next"]["cue"])
            self.assertIsNone(result["next"]["corner_name"])
            self.assertFalse(result["voice_ready"])

    def test_wrong_layout_missing_identity_and_length_mismatch_do_not_use_map(self):
        for changes in ({"track_id": None}, {"track_id": 128}, {"layout": "Short Course"},
                        {"length_m": None}, {"length_m": 3500}):
            result = corner_guidance(guide(.0675, **changes))
            self.assertFalse(result["voice_ready"])
            self.assertEqual("unknown", result["next"]["location_basis"])

    def test_medium_is_not_claimed_to_be_half_physical_pedal_and_missing_brake_stays_unknown(self):
        state = guide(.0675)
        state["next"].update(initial_brake_class="media", reference_brake_peak=.5)
        result = corner_guidance(state)
        self.assertIn("Freno medio", result["next"]["cue"])
        self.assertNotIn("medio pedal", result["next"]["cue"])
        self.assertNotIn("corner_name", state["next"])
        state["next"]["reference_brake_peak"] = None
        self.assertIsNone(corner_guidance(state)["next"]["cue"])
        state["next"] = None
        self.assertFalse(corner_guidance(state)["voice_ready"])

    def test_existing_not_ready_gate_is_preserved(self):
        state = guide(.0675)
        state["voice_ready"] = False
        self.assertFalse(corner_guidance(state)["voice_ready"])
