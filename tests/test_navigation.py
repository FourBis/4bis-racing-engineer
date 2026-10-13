from dataclasses import replace
import unittest

from racing_engineer.model import Sample
from racing_engineer.navigation import Navigator


def make_lap(start: float, duration: float, *, session="s1", speed=30.0,
             track=True, replay=False, incidents=0, damage="healthy",
             metadata=True, braking=True, lap_number=1, boundary_progress=0.0,
             track_length_m=1000.0, source="demo", brake_bias=58.0, brake_bias_unit="%"):
    rows = []
    for i in range(101):
        pct = boundary_progress if i == 100 else i / 100
        brake = 0.0
        if braking and i in (10, 40, 70):
            brake = 0.2 + i / 500
        elif braking and any(on < i <= on + 3 for on in (10, 40, 70)):
            brake = 0.8
        rows.append(Sample(
            source=source, session_id=session, time_s=start + duration * i / 100,
            lap=lap_number, lap_dist_pct=pct, speed_mps=speed, brake=brake,
            throttle=0.0, on_track=track, is_replay=replay, incidents=incidents,
            damage=damage,
            brake_bias=brake_bias, brake_bias_unit=brake_bias_unit,
            track_id=1 if metadata else None, track_name="Circuit" if metadata else None,
            track_config="GP" if metadata else None,
            track_length_m=track_length_m if metadata else None,
            car_id=2 if metadata else None, car_name="Car" if metadata else None,
        ))
    return rows


def feed(nav, *laps):
    for lap in laps:
        for index, sample in enumerate(lap):
            identity = (sample.source, sample.session_id, sample.track_id, sample.track_config,
                        sample.car_id, sample.track_length_m)
            old_identity = ((nav._sample.source, nav._sample.session_id, nav._sample.track_id,
                             nav._sample.track_config, nav._sample.car_id,
                             nav._sample.track_length_m) if nav._sample else None)
            if index == 0 and nav._sample is not None and nav._sample.time_s == sample.time_s and identity == old_identity:
                continue
            nav.update(sample)


class NavigationTests(unittest.TestCase):
    def test_fastest_completed_lap_is_reference_and_latest_is_compared_to_prior_reference(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10, lap_number=1), make_lap(10, 9, lap_number=2),
             make_lap(19, 9.5, lap_number=3))
        state = nav.snapshot()
        self.assertEqual(state["reference"]["time_s"], 9.0)
        self.assertEqual(state["reference"]["kind"], "personal_observed")
        self.assertEqual(state["reference"]["basis"], "fastest_observed_lap")
        self.assertFalse(state["reference"]["target_validated"])
        self.assertEqual(state["comparison"]["lap"], 3)
        self.assertGreater(state["comparison"]["delta_s"], 0)
        self.assertIn(state["comparison"]["worst_segment"]["start_pct"], (0, 0.25, 0.5, 0.75))
        self.assertTrue(state["zones"])
        self.assertTrue(all(set(zone) >= {"id", "lap_dist_pct", "reference_speed_kph",
                                          "reference_brake_peak", "initial_brake_class", "braking_start_m",
                                          "reference_approach_speed_kph", "reference_brake_bias"}
                            for zone in state["zones"]))
        self.assertIn("no óptima", state["status"])

    def test_reference_brake_profile_uses_command_peak_thresholds_and_keeps_missing_unknown(self):
        nav = Navigator()
        rows = make_lap(0, 10)
        for onset, peak in zip((10, 40, 70), (0.65, 0.35, 0.34)):
            for index in range(onset, onset + 4):
                rows[index] = replace(rows[index], brake=peak)
        feed(nav, rows)
        zones = nav.snapshot()["zones"]
        self.assertEqual([(zone["reference_brake_peak"], zone["initial_brake_class"])
                          for zone in zones], [(0.65, "fuerte"), (0.35, "media"), (0.34, "suave")])

        unknown = Navigator()
        feed(unknown, [replace(sample, brake=None) for sample in make_lap(0, 10)])
        self.assertIsNotNone(unknown.snapshot()["reference"])
        self.assertEqual(unknown.snapshot()["zones"], [])

    def test_traffic_and_a_slower_lap_keep_the_reference_guidance_available(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10, source="iracing"))
        baseline = nav.snapshot()
        # Synthetic race adaptation: slower lap, less brake, reported traffic.
        rows = [replace(s, traffic="traffic", brake=s.brake * .5)
                for s in make_lap(10, 11, source="iracing", lap_number=2)]
        for sample in rows[1:]:
            nav.update(sample)
            state = nav.snapshot()
            self.assertTrue(state["voice_ready"])
            self.assertIsNotNone(state["next"])
            self.assertEqual(baseline["zones"], state["zones"])
            self.assertEqual(baseline["reference"], state["reference"])
        self.assertGreater(nav.snapshot()["comparison"]["delta_s"], 0)

    def test_observed_start_boundary_can_be_at_nonzero_progress(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10, lap_number=1, boundary_progress=0.002),
             make_lap(10, 9, lap_number=2, boundary_progress=0.002))
        self.assertLess(nav.snapshot()["reference"]["time_s"], 9.1)

    def test_boundary_sample_lap_counter_cannot_advance_the_completed_lap_number(self):
        nav = Navigator()
        rows = make_lap(0, 10, lap_number=7)
        rows[-1] = replace(rows[-1], lap=8)
        feed(nav, rows)
        self.assertEqual(nav.snapshot()["reference"]["lap"], 7)

    def test_rejects_invalid_lap_evidence_including_the_finish_sample(self):
        mutations = (
            lambda rows: rows.__setitem__(50, replace(rows[50], time_s=rows[49].time_s + 0.6)),
            lambda rows: rows.__setitem__(50, replace(rows[50], speed_mps=0.0)),
            lambda rows: rows.__setitem__(50, replace(rows[50], damage="damaged")),
            lambda rows: rows.__setitem__(50, replace(rows[50], incidents=1)),
            lambda rows: rows.__setitem__(50, replace(rows[50], incidents=None)),
            lambda rows: rows.__setitem__(50, replace(rows[50], on_track=False)),
            lambda rows: rows.__setitem__(50, replace(rows[50], is_replay=True)),
            lambda rows: rows.__setitem__(50, replace(rows[50], lap_dist_pct=None)),
            lambda rows: rows.__setitem__(50, replace(rows[50], lap_dist_pct=0.1)),
            lambda rows: rows.__setitem__(100, replace(rows[100], incidents=1)),
            lambda rows: rows.__setitem__(100, replace(rows[100], on_track=False)),
            lambda rows: rows.__setitem__(100, replace(rows[100], damage="damaged")),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                nav = Navigator()
                rows = make_lap(0, 10)
                mutate(rows)
                feed(nav, rows)
                self.assertIsNone(nav.snapshot()["reference"])

    def test_brake_ramp_and_constant_nonzero_incidents_after_repair_are_allowed(self):
        nav = Navigator()
        rows = make_lap(0, 10, incidents=3, damage="unknown")
        for index, value in ((10, 0.07), (11, 0.14), (12, 0.21)):
            rows[index] = replace(rows[index], brake=value)
        rows[13] = replace(rows[13], brake=0.8)
        feed(nav, rows)
        self.assertIsNotNone(nav.snapshot()["reference"])
        self.assertEqual(nav.snapshot()["zones"][0]["lap_dist_pct"], 0.12)

    def test_braking_zone_requires_observed_low_before_onset(self):
        for initial_brakes, missing in ((None, True), (0.8, False)):
            with self.subTest(initial_brakes=initial_brakes):
                nav = Navigator()
                rows = make_lap(0, 10)
                if missing:
                    rows[9] = replace(rows[9], brake=None)
                else:
                    for index in range(15):
                        rows[index] = replace(rows[index], brake=initial_brakes)
                    rows[15] = replace(rows[15], brake=0.0)
                feed(nav, rows)
                self.assertEqual(nav.snapshot()["zones"][0]["lap_dist_pct"], 0.4)

    def test_braking_zone_with_missing_sample_is_discarded_and_valid_zones_remain(self):
        nav = Navigator()
        rows = make_lap(0, 10)
        rows[41] = replace(rows[41], brake=None)
        feed(nav, rows)
        self.assertEqual([zone["lap_dist_pct"] for zone in nav.snapshot()["zones"]], [0.1, 0.7])

    def test_braking_zone_without_observed_release_at_lap_end_is_discarded(self):
        nav = Navigator()
        rows = make_lap(0, 10)
        for index in range(70, 100):
            rows[index] = replace(rows[index], brake=0.8)
        feed(nav, rows)
        self.assertEqual([zone["lap_dist_pct"] for zone in nav.snapshot()["zones"]], [0.1, 0.4])

    def test_session_identity_reset_and_unknown_metadata(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10, metadata=False))
        state = nav.snapshot()
        self.assertIsNotNone(state["reference"])
        self.assertIsNone(state["next"]["distance_m"])
        self.assertIsNone(state["next"]["seconds_ahead"])
        self.assertFalse(state["voice_ready"])
        feed(nav, make_lap(10, 9, session="s2", metadata=False))
        self.assertEqual(nav.snapshot()["reference"]["time_s"], 9)
        feed(nav, make_lap(19, 8.5, session="s2", metadata=True))
        self.assertEqual(nav.snapshot()["reference"]["time_s"], 8.5)
        self.assertEqual(nav.snapshot()["reference"]["lap"], 1)
        feed(nav, make_lap(27.5, 8.0, session="s2", metadata=True, track_length_m=1200.0))
        self.assertEqual(nav.snapshot()["reference"]["time_s"], 8.0)

    def test_delta_and_next_zone_are_suppressed_in_pits(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10))
        for i in range(1, 31):
            nav.update(replace(nav._sample, time_s=10.0 + i / 10, lap_dist_pct=i / 100,
                               brake=0.0, speed_mps=30.0))
        current = nav._sample
        state = nav.snapshot()
        self.assertIsNotNone(state["next"])
        self.assertEqual(state["next"]["distance_m"], 100.0)
        self.assertEqual((state["next"]["reference_brake_peak"], state["next"]["initial_brake_class"]),
                         (0.8, "fuerte"))
        self.assertIsNotNone(state["next"]["seconds_ahead"])
        nav.update(replace(current, time_s=current.time_s + 0.1, speed_mps=0.0))
        self.assertIsNone(nav.snapshot()["next"])
        self.assertIsNone(nav.snapshot()["delta_s"])

    def test_wrapped_zone_is_reported_with_lap_distance_and_horizon(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10))
        for i in range(1, 96):
            nav.update(replace(nav._sample, time_s=10.0 + i / 10, lap_dist_pct=i / 100,
                               brake=0.0, speed_mps=10.0))
        self.assertEqual(nav.snapshot()["next"]["id"], 1)
        self.assertEqual(nav.snapshot()["next"]["distance_m"], 150.0)
        self.assertEqual(nav.snapshot()["next"]["seconds_ahead"], 15.0)

    def test_audio_ready_requires_live_reference_identity_and_on_track_sample(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10, source="iracing"))
        self.assertTrue(nav.snapshot()["voice_ready"])
        nav.update(replace(nav._sample, time_s=10.1, speed_mps=0.0))
        self.assertFalse(nav.snapshot()["voice_ready"])

    def test_time_reversal_clears_reference_and_lap_token(self):
        nav = Navigator()
        feed(nav, make_lap(0, 10))
        nav.update(make_lap(10, 10)[1])
        token = nav.snapshot()["lap_token"]
        nav.update(replace(make_lap(10, 10)[2], time_s=5.0))
        self.assertIsNone(nav.snapshot()["reference"])
        self.assertNotEqual(nav.snapshot()["lap_token"], token)
