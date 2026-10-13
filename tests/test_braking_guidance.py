"""Synthetic evidence only: no recorded laps or claimed optimum braking target."""
from dataclasses import replace
import unittest

from racing_engineer.navigation import Navigator
from test_navigation import feed, make_lap


class BrakingGuidanceTests(unittest.TestCase):
    def test_ramp_uses_prior_speed_and_brackets_start_from_finish(self):
        rows = make_lap(0, 10)
        for i, brake in ((10, .07), (11, .14), (12, .21), (13, .823)):
            rows[i] = replace(rows[i], brake=brake, speed_mps=20)
        rows[9] = replace(rows[9], speed_mps=40)
        nav = Navigator()
        feed(nav, rows)
        zone = nav.snapshot()["zones"][0]
        self.assertEqual(144, zone["reference_approach_speed_kph"])
        self.assertEqual(72, zone["reference_speed_kph"])
        self.assertEqual(80, zone["reference_brake_peak_pct"])
        self.assertEqual({"min": 90, "max": 120, "anchor": "start_finish_line",
                          "basis": "last_low_to_threshold"}, zone["braking_start_m"])
        self.assertEqual(120, nav.snapshot()["next"]["distance_m"])
        self.assertEqual(3, nav.snapshot()["next"]["seconds_to_braking_start"])

    def test_current_speed_matches_same_progress_not_future_braking_speed(self):
        rows = make_lap(0, 10)
        rows[9] = replace(rows[9], speed_mps=50)
        nav = Navigator()
        feed(nav, rows)
        point = nav.snapshot()["next"]
        self.assertEqual(180, point["reference_approach_speed_kph"])
        self.assertEqual(108, point["matched_reference_speed_kph"])
        self.assertTrue(point["numeric_guidance_ready"])
        for speed in (20, 40):
            nav.update(replace(nav._sample, time_s=nav._sample.time_s + .1, speed_mps=speed,
                               lap_dist_pct=.005))
            self.assertFalse(nav.snapshot()["next"]["numeric_guidance_ready"])
            self.assertIn("Velocidad distinta", nav.snapshot()["next"]["guidance_reason"])
        nav.update(replace(nav._sample, time_s=nav._sample.time_s + .1, speed_mps=30,
                           lap_dist_pct=.01))
        self.assertTrue(nav.snapshot()["next"]["numeric_guidance_ready"])

    def test_missing_bias_has_historical_numbers_but_no_target(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10, brake_bias=None, brake_bias_unit=None))
        point = nav.snapshot()["next"]
        self.assertEqual(80, point["reference_brake_peak_pct"])
        self.assertFalse(point["numeric_guidance_ready"])
        self.assertIn("Brake bias no disponible", point["guidance_reason"])

    def test_bias_or_unit_change_resets_reference_and_can_learn_a_slower_lap(self):
        for bias, unit in ((59, "%"), (58, "offset"), (None, None)):
            with self.subTest(bias=bias, unit=unit):
                nav = Navigator()
                feed(nav, make_lap(0, 10))
                nav.update(replace(nav._sample, time_s=10.1, lap_dist_pct=.01,
                                   brake_bias=bias, brake_bias_unit=unit))
                self.assertIsNone(nav.snapshot()["reference"])
                # A complete lap after the new boundary can be slower and still qualify.
                feed(nav, make_lap(10.2, 11, brake_bias=bias, brake_bias_unit=unit),
                     make_lap(21.2, 11, brake_bias=bias, brake_bias_unit=unit))
                self.assertEqual(11, nav.snapshot()["reference"]["time_s"])
                self.assertEqual(bias, nav.snapshot()["zones"][0]["reference_brake_bias"])

    def test_signed_bias_with_unconfirmed_unit_is_only_an_opaque_matching_setting(self):
        for unit in (None, "", "offset"):
            nav = Navigator()
            feed(nav, make_lap(0, 10, brake_bias=-2.5, brake_bias_unit=unit))
            point = nav.snapshot()["next"]
            self.assertTrue(point["numeric_guidance_ready"])
            self.assertEqual(-2.5, point["reference_brake_bias"])
            self.assertEqual(unit, point["reference_brake_bias_unit"])
            self.assertEqual("limited", point["confidence"])

    def test_slow_ramp_is_not_an_accurate_onset(self):
        rows = make_lap(0, 10)
        for i in range(5, 10):
            rows[i] = replace(rows[i], brake=.1)
        nav = Navigator()
        feed(nav, rows)
        point = nav.snapshot()["next"]
        self.assertFalse(point["numeric_guidance_ready"])
        self.assertIn("poco acotado", point["guidance_reason"])

    def test_small_progress_reversal_cannot_create_inverted_start_bracket(self):
        rows = make_lap(0, 10, track_length_m=4056.9)
        rows[10] = replace(rows[10], lap_dist_pct=.08)
        nav = Navigator()
        feed(nav, rows)
        self.assertEqual([.4, .7], [z["lap_dist_pct"] for z in nav.snapshot()["zones"]])

    def test_missing_identity_never_presents_a_comparable_numeric_reference(self):
        nav = Navigator()
        feed(nav, [replace(s, car_id=None) for s in make_lap(0, 10)])
        self.assertFalse(nav.snapshot()["next"]["numeric_guidance_ready"])
        self.assertIn("identidad", nav.snapshot()["next"]["guidance_reason"])

    def test_boxes_moving_and_identity_changes_remove_guidance(self):
        for changes in ({"on_pit_road": True}, {"track_id": 2}, {"track_config": "Other"},
                        {"car_id": 7}, {"track_length_m": 1001}, {"damage": "damaged"}):
            nav = Navigator()
            feed(nav, make_lap(0, 10))
            nav.update(replace(nav._sample, time_s=10.1, lap_dist_pct=.01, **changes))
            self.assertIsNone(nav.snapshot()["next"])
        rows = make_lap(0, 10)
        rows[50] = replace(rows[50], on_pit_road=True)
        nav = Navigator()
        feed(nav, rows)
        self.assertIsNone(nav.snapshot()["reference"])

    def test_occurrence_and_distance_survive_finish_crossing(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10))
        for i in range(1, 100):
            nav.update(replace(nav._sample, time_s=10 + i / 10, lap_dist_pct=i / 100, brake=0))
        before = nav.snapshot()["next"]
        nav.update(replace(nav._sample, time_s=20, lap_dist_pct=0, brake=0))
        after = nav.snapshot()["next"]
        self.assertEqual(before["occurrence_token"], after["occurrence_token"])
        self.assertEqual(110, before["distance_m"])
        self.assertEqual(100, after["distance_m"])
        self.assertEqual(before["braking_start_m"], after["braking_start_m"])

    def test_exact_one_to_zero_progress_boundary_does_not_divide_by_zero(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10)[:-1])
        nav.update(replace(nav._sample, time_s=10, lap_dist_pct=1.0, brake=0))
        nav.update(replace(nav._sample, time_s=10.1, lap_dist_pct=0.0, brake=0))
        self.assertIsNotNone(nav.snapshot()["reference"])
        self.assertIsNotNone(nav.snapshot()["lap_token"])
