import unittest
from dataclasses import replace

from racing_engineer.analysis import _lap_groups
from racing_engineer.corner_review import corner_review
from racing_engineer.model import Sample
from racing_engineer.race_results import race_results


def samples_with_loss(*, count=1000, controls=True):
    samples, clock = [], 0.0
    for lap in (1, 2):
        for index in range(count):
            progress = index / count
            samples.append(Sample(
                source="demo", session_id="corners", time_s=clock, lap=lap,
                lap_dist_pct=progress, speed_mps=45, brake=0.2 if controls else None,
                throttle=0.6 if controls else None, abs_active=None,
                on_track=True, is_replay=False, incidents=0,
                on_pit_road=False, car_id=119, track_id=127,
                track_name="Road Atlanta", track_config="Full Course", track_length_m=4056.9,
            ))
            clock += 90 / count + (10 / count if lap == 2 and 0.16 <= progress < 0.30 else 0)
    samples.append(replace(samples[-1], time_s=clock, lap=3, lap_dist_pct=0))
    return samples


def laps(samples):
    return [lap for lap in _lap_groups(samples) if lap.complete][:2]


class CornerReviewTests(unittest.TestCase):
    def test_zone_loss_is_local_and_later_zones_do_not_repeat_accumulated_delta(self):
        result = corner_review(*laps(samples_with_loss()))
        self.assertTrue(result["available"])
        self.assertEqual(result["top_zone_ids"], ["t2-t4"])
        zones = {zone["id"]: zone for zone in result["zones"]}
        self.assertAlmostEqual(zones["t2-t4"]["delta_s"], 1.4)
        self.assertAlmostEqual(zones["t5"]["delta_s"], 0)
        self.assertAlmostEqual(sum(z["delta_s"] for z in result["zones"]), 1.4)
        self.assertEqual(zones["t2-t4"]["start_pct"], 16)
        self.assertEqual(zones["t2-t4"]["end_pct"], 30)
        self.assertIn("pierdes 1.400 s", zones["t2-t4"]["observation"])
        self.assertIn("referencia 1", zones["t2-t4"]["observation"])

    def test_replay_uses_elapsed_time_from_each_finish_line_not_equal_progress(self):
        first, second = corner_review(*laps(samples_with_loss()))["replay"].values()
        a = next(frame for frame in first["frames"] if frame[1] == 50)
        b = next(frame for frame in second["frames"] if frame[1] == 50)
        self.assertAlmostEqual(a[0], 45)
        self.assertAlmostEqual(b[0], 46.4)
        for replay in (first, second):
            self.assertEqual(replay["frames"][0][:2], [0, 0])
            self.assertEqual(replay["frames"][-1][:2], [replay["duration_s"], 100])
            self.assertEqual(replay["frames"][-1][2:], [None, None, None])
            self.assertTrue(all(a[0] < b[0] for a, b in zip(replay["frames"], replay["frames"][1:])))

    def test_missing_inputs_stay_null_in_observations_stats_and_frames(self):
        result = corner_review(*laps(samples_with_loss(controls=False)))
        zone = next(z for z in result["zones"] if z["id"] == "t2-t4")
        self.assertIsNone(zone["first"]["brake_mean_pct"])
        self.assertIn("faltan datos de freno, gas", zone["observation"])
        self.assertIn("canales observados", zone["observation"])
        self.assertTrue(all(frame[3:] == [None, None]
                            for replay in result["replay"].values() for frame in replay["frames"]))
        self.assertIsNone(result["zones"][-1]["first"]["speed_exit_kmh"])

    def test_unknown_or_incompatible_map_or_car_is_not_presented(self):
        source = samples_with_loss()
        for name, value in (("track_id", 128), ("track_config", None),
                            ("track_config", "Short Course"), ("track_length_m", None),
                            ("track_length_m", 4200), ("car_id", None), ("car_id", 999)):
            with self.subTest(name=name, value=value):
                changed = [replace(s, **{name: value}) if s.lap == 2 else s for s in source]
                result = corner_review(*laps(changed))
                self.assertFalse(result["available"])
                self.assertTrue(result["reason"])
                self.assertNotIn("map", result)

    def test_maximum_frames_and_no_diagnosis_or_optimal_target(self):
        result = corner_review(*laps(samples_with_loss(count=4000)))
        for replay in result["replay"].values():
            self.assertLessEqual(len(replay["frames"]), 1200)
        for zone in result["zones"]:
            text = zone["observation"] + zone["next_test"]
            self.assertIn("preparación", text)
            self.assertNotIn("patinaje", text)
            self.assertNotIn("bloqueo", text)
            self.assertNotIn("óptimo", text)
        self.assertIn("sin GPS", result["note"])
        self.assertIn("no son entradas", result["note"])

    def test_decimation_keeps_both_edges_of_isolated_and_sustained_channel_gaps(self):
        source = samples_with_loss(count=4000)
        gaps = (("brake", 3, 602, 602), ("speed_mps", 2, 1002, 1020),
                ("throttle", 4, 1502, 1502))
        for channel, _, start, end in gaps:
            for index in range(start, end + 1):
                source[index] = replace(source[index], **{channel: None})
        replay = corner_review(*laps(source))["replay"]["first"]
        frames = {frame[0]: frame for frame in replay["frames"]}
        for channel, column, start, end in gaps:
            with self.subTest(channel=channel):
                for index in (start - 1, start, end, end + 1):
                    self.assertTrue(source[index].time_s in frames,
                                    f"Falta el borde de {channel} en muestra {index}")
                    value = frames[source[index].time_s][column]
                    if start <= index <= end:
                        self.assertIsNone(value)
                    else:
                        self.assertIsNotNone(value)
        self.assertLess(len(frames), 1200)

    def test_finish_frame_clears_channels_even_when_sdk_observed_exactly_one(self):
        source = samples_with_loss()
        source[999] = replace(source[999], lap_dist_pct=1, brake=0.4, throttle=0.8)
        first, second = laps(source)
        self.assertEqual(first.samples[-1].lap_dist_pct, 1)
        self.assertIsNotNone(first.samples[-1].throttle)
        frame = corner_review(first, second)["replay"]["first"]["frames"][-1]
        self.assertEqual(frame[:2], [first.end_time_s - first.start_time_s, 100])
        self.assertEqual(frame[2:], [None, None, None])

    def test_race_results_includes_review_for_selected_pair(self):
        result = race_results(samples_with_loss())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["corner_review"]["available"])
        self.assertEqual(result["corner_review"]["top_zone_ids"], ["t2-t4"])

    def test_human_explanation_compares_slower_entry_with_faster_exit_without_cause(self):
        source = samples_with_loss()
        changed = [replace(s, speed_mps=48 if s.lap_dist_pct == 0.16 else 41)
                   if s.lap == 2 and s.lap_dist_pct in (0.16, 0.30) else s for s in source]
        result = corner_review(*laps(changed))
        zone = next(z for z in result["zones"] if z["id"] == "t2-t4")
        self.assertIn("referencia 1 llegaste 10.8 km/h más lento", zone["observation"])
        self.assertIn("saliste 14.4 km/h más rápido", zone["observation"])
        self.assertIn("vuelta 2 pierdes 1.400 s", zone["observation"])
        self.assertIn("antes de intentar entrar más rápido", zone["next_test"])
        for unsupported in ("frenaste antes", "aceleraste antes", "porque", "patinaje", "daño", "tráfico"):
            self.assertNotIn(unsupported, zone["observation"])

    def test_no_speed_data_explains_missing_evidence_and_preserves_time(self):
        source = [replace(s, speed_mps=None, brake=None) for s in samples_with_loss()]
        zone = next(z for z in corner_review(*laps(source))["zones"] if z["id"] == "t2-t4")
        self.assertIn("pierdes 1.400 s", zone["observation"])
        self.assertIn("faltan datos de velocidad de entrada, velocidad de salida, freno", zone["observation"])
        self.assertNotIn("más rápido", zone["observation"])
        self.assertIn("solo los canales disponibles", zone["next_test"])

    def test_other_speed_patterns_keep_the_direction_of_the_evidence(self):
        source = samples_with_loss()
        for entry, exit_, expected in ((45, 41, "referencia 1 saliste 14.4 km/h más rápido"),
                                       (48, 49, "vuelta 2 saliste 14.4 km/h más rápido"),
                                       (42, 45, "vuelta 2 llegaste 10.8 km/h más lento")):
            with self.subTest(entry=entry, exit=exit_):
                changed = [replace(s, speed_mps=entry if s.lap_dist_pct == 0.16 else exit_)
                           if s.lap == 2 and s.lap_dist_pct in (0.16, 0.30) else s for s in source]
                zone = next(z for z in corner_review(*laps(changed))["zones"] if z["id"] == "t2-t4")
                self.assertIn(expected, zone["observation"])
                self.assertIn("pierdes 1.400 s", zone["observation"])


if __name__ == "__main__":
    unittest.main()
