import unittest
from dataclasses import replace

from racing_engineer.model import Sample
from racing_engineer.race_results import race_results


def session(*, count=3, session_id="race", loss_lap=None, loss_segment=7):
    """200 muestras por vuelta, 20 s base; tramo lento añade exactamente 1 s."""
    samples, clock = [], 0.0
    for lap in range(1, count + 1):
        for index in range(200):
            samples.append(Sample(
                source="demo", session_id=session_id, time_s=clock, lap=lap,
                lap_dist_pct=index / 200, speed_mps=30, brake=0.2,
                throttle=0.4, steering_wheel_rad=0.0, abs_active=False,
                on_track=True, is_replay=False, incidents=4,
                track_id=1, track_config="GP", car_id=2,
                on_pit_road=False,
            ))
            clock += 0.2 if lap == loss_lap and index // 10 == loss_segment else 0.1
    samples.append(replace(samples[-1], time_s=clock, lap=count + 1, lap_dist_pct=0))
    return samples


class RaceResultsTests(unittest.TestCase):
    def test_incident_on_finish_tick_prevents_previous_lap_reference(self):
        samples = [replace(s, incidents=6 if s.lap >= 2 else 4)
                   for s in session(count=3, loss_lap=2)]
        result = race_results(samples)
        first = result["laps"][0]
        self.assertTrue(first["review_eligible"])
        self.assertEqual(first["metrics"]["incidents_delta"], 2)
        self.assertNotEqual(result["best"]["lap"], 1)
        self.assertEqual(result["laps"][1]["metrics"]["incidents_delta"], 0)

    def test_missing_or_reset_counter_at_crossing_is_not_a_clean_reference(self):
        for counter in (None, 1):
            with self.subTest(counter=counter):
                samples = session(count=3, loss_lap=2)
                samples[200] = replace(samples[200], incidents=counter)
                result = race_results(samples)
                self.assertIsNone(result["laps"][0]["metrics"]["incidents_delta"])
                self.assertNotEqual(result["best"]["lap"], 1)

    def test_error_lap_remains_eligible_but_pits_and_stop_are_excluded(self):
        samples = session(count=4, loss_lap=2)
        for i, sample in enumerate(samples):
            if sample.lap == 1:
                samples[i] = replace(sample, on_pit_road=True)
            elif sample.lap == 3 and i % 200 < 30:
                samples[i] = replace(sample, speed_mps=0)
            elif sample.lap == 2 and i % 200 >= 80:
                samples[i] = replace(sample, incidents=6, damage="damaged")
        samples = [replace(s, incidents=6) if s.lap >= 3 else s for s in samples]
        result = race_results(samples)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["best"]["lap"], 4)
        self.assertEqual(result["worst"]["lap"], 2)
        self.assertEqual(result["worst"]["metrics"]["incidents_delta"], 2)
        self.assertIn("boxes", result["laps"][0]["exclusion_reason"])
        self.assertIn("detención", result["laps"][2]["exclusion_reason"])
        self.assertTrue(any("No acredita daño" in text for text in result["worst"]["context"]))

    def test_unknown_context_and_accumulated_incidents_allow_description(self):
        samples = [replace(s, on_pit_road=None) for s in session(count=2)]
        result = race_results(samples)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["best"]["metrics"]["incidents_delta"], 0)
        self.assertIn("Daño: desconocido.", result["best"]["context"])
        self.assertIn("Tráfico: desconocido.", result["best"]["context"])
        self.assertTrue(any("boxes desconocido" in text for text in result["best"]["context"]))
        self.assertTrue(any("No se puede descartar paso por boxes en movimiento" in text
                            for text in result["limitations"]))
        self.assertIn("no certifica", result["comparison"]["limitations"])

    def test_only_last_session_block_and_latest_identity_are_ranked(self):
        old = session(count=2, session_id="old")
        new = session(count=3, session_id="new", loss_lap=3)
        new = [replace(s, car_id=99) if s.lap >= 2 else s for s in new]
        result = race_results(old + new)
        self.assertEqual(result["session_id"], "new")
        self.assertTrue(all(row["id"].startswith("demo:new:") for row in result["laps"]))
        self.assertIn("identidad", result["laps"][0]["exclusion_reason"])
        self.assertEqual((result["best"]["lap"], result["worst"]["lap"]), (2, 3))
        # Una sesión que reaparece no recupera sus vueltas del bloque anterior.
        again = [replace(s, session_id="old") for s in new[-10:]]
        self.assertEqual(race_results(old + new + again)["status"], "insufficient")

    def test_segment_delta_is_local_not_accumulated_and_metrics_are_weighted(self):
        samples = session(count=2, loss_lap=2, loss_segment=7)
        for i, sample in enumerate(samples):
            if sample.lap == 2 and 70 <= i % 200 <= 80:
                samples[i] = replace(sample, brake=0.5, throttle=0.1,
                                     abs_active=True, speed_mps=25)
        result = race_results(samples)
        segment = result["segments"][0]
        self.assertAlmostEqual(result["delta_s"], 1)
        self.assertEqual((segment["start_pct"], segment["end_pct"]), (35, 40))
        self.assertAlmostEqual(segment["delta_s"], 1)
        self.assertAlmostEqual(segment["first"]["duration_s"], 1)
        self.assertAlmostEqual(segment["second"]["duration_s"], 2)
        self.assertAlmostEqual(segment["second"]["speed_mean_kmh"], 90)
        self.assertAlmostEqual(segment["second"]["speed_entry_kmh"], 90)
        self.assertAlmostEqual(segment["second"]["speed_exit_kmh"], 90)
        self.assertAlmostEqual(segment["second"]["brake_mean_pct"], 50)
        self.assertAlmostEqual(segment["second"]["throttle_mean_pct"], 10)
        self.assertAlmostEqual(segment["second"]["abs_active_s"], 2)
        self.assertEqual(len(result["segments"]), 1)

    def test_nullable_channels_remain_null_in_stats_and_plot(self):
        samples = [replace(s, brake=None, throttle=None, speed_mps=None,
                           abs_active=None) for s in session(count=2, loss_lap=2)]
        result = race_results(samples)
        self.assertEqual(result["status"], "ok")
        second = result["segments"][0]["second"]
        self.assertTrue(any("no se pueden descartar detenciones" in text for text in result["limitations"]))
        for key in ("speed_mean_kmh", "speed_entry_kmh", "speed_exit_kmh", "brake_mean_pct", "throttle_mean_pct",
                    "abs_active_s", "coast_s", "brake_throttle_overlap_s"):
            self.assertIsNone(second[key])
        self.assertIsNone(result["comparison"]["points"][50]["second"]["brake"])

    def test_mean_uses_time_not_number_of_samples(self):
        samples = session(count=2, loss_lap=2, loss_segment=7)
        for i, sample in enumerate(samples):
            if sample.lap == 2 and 70 <= i % 200 <= 80:
                samples[i] = replace(sample, brake=0.2 if i % 200 < 75 else 0.8,
                                     time_s=sample.time_s + (0.1 if i % 200 == 75 else 0))
        segment = race_results(samples)["segments"][0]
        self.assertAlmostEqual(segment["second"]["brake_mean_pct"], 51.5)

    def test_replay_unknown_track_gap_and_incomplete_are_excluded(self):
        for field, value, expected in (("is_replay", True, "replay"),
                                        ("is_replay", None, "replay"),
                                        ("on_track", None, "estado en pista")):
            with self.subTest(field=field, value=value):
                samples = [replace(s, **{field: value}) if s.lap == 1 else s
                           for s in session(count=2)]
                result = race_results(samples)
                self.assertEqual(result["status"], "insufficient")
                self.assertIn(expected, result["laps"][0]["exclusion_reason"])
        samples = session(count=2)
        samples = [replace(s, time_s=s.time_s + (1 if i >= 50 else 0))
                   for i, s in enumerate(samples)]
        self.assertIn("brecha", race_results(samples)["laps"][0]["exclusion_reason"])
        result = race_results(session(count=2)[:-1])
        self.assertEqual(result["status"], "insufficient")
        self.assertIn("incompleta", result["laps"][-1]["exclusion_reason"])

    def test_teleport_and_clock_reset_do_not_mix_runs(self):
        samples = session(count=2)
        samples[80] = replace(samples[80], lap_dist_pct=0.7)
        result = race_results(samples)
        self.assertEqual(result["status"], "insufficient")
        self.assertFalse(result["laps"][0]["review_eligible"])
        before = session(count=2)
        after = session(count=2, loss_lap=2)
        result = race_results(before + after)
        self.assertEqual(result["status"], "ok")
        self.assertIn("run1", result["best"]["id"])
        self.assertIn("run1", result["worst"]["id"])

    def test_empty_or_one_complete_lap_does_not_claim_best_and_worst(self):
        for samples in ([], session(count=1)):
            result = race_results(samples)
            self.assertEqual(result["status"], "insufficient")
            self.assertIsNone(result["best"])
            self.assertIsNone(result["worst"])
            self.assertEqual(result["comparison"]["points"], [])

    def test_fast_lap_with_new_incidents_is_reviewable_but_not_reference(self):
        samples = session(count=2, loss_lap=2)
        samples = [replace(s, incidents=6) if s.lap == 1 and s.lap_dist_pct >= 0.5 else s
                   for s in samples]
        result = race_results(samples)
        self.assertEqual(result["best"]["lap"], 2)
        self.assertEqual(result["status"], "insufficient")
        self.assertIsNone(result["worst"])
        self.assertTrue(any("igual o más lenta" in text for text in result["limitations"]))
        fast_id = next(row["id"] for row in result["laps"] if row["lap"] == 1)
        manual = race_results(samples, fast_id)
        self.assertEqual(manual["selection"], "manual")
        self.assertEqual(manual["worst"]["lap"], 1)
        self.assertTrue(manual["worst"]["review_eligible"])
        self.assertAlmostEqual(manual["delta_s"], -1)
        unknown = race_results([replace(s, incidents=None) for s in session(count=2)])
        self.assertEqual(unknown["status"], "insufficient")
        self.assertIsNone(unknown["best"])
        self.assertTrue(all(row["review_eligible"] for row in unknown["laps"][:-1]))
        self.assertTrue(any("No hay referencia" in text for text in unknown["limitations"]))

    def test_automatic_ties_are_distinct_and_nonnegative(self):
        result = race_results(session(count=2))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["selection"], "automatic")
        self.assertNotEqual(result["best"]["id"], result["worst"]["id"])
        self.assertEqual(result["delta_s"], 0)
        self.assertIsInstance(result["comparison"]["limitations"], str)

    def test_manual_invalid_ineligible_and_reference_are_rejected(self):
        samples = session(count=3, loss_lap=3)
        result = race_results(samples)
        incomplete_id = result["laps"][-1]["id"]
        for requested in ("unknown", incomplete_id, result["best"]["id"]):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                race_results(samples, requested)
        pits = [replace(s, on_pit_road=True) if s.lap == 3 else s for s in samples]
        with self.assertRaises(ValueError):
            race_results(pits, result["worst"]["id"])
        with self.assertRaises(ValueError):
            race_results([], "unknown")

    def test_manual_selects_eligible_lap_instead_of_automatic_worst(self):
        samples = session(count=3, loss_lap=3)
        result = race_results(samples)
        other = next(row for row in result["laps"] if row["review_eligible"]
                     and row["id"] not in (result["best"]["id"], result["worst"]["id"]))
        manual = race_results(samples, other["id"])
        self.assertEqual(manual["status"], "ok")
        self.assertEqual(manual["selection"], "manual")
        self.assertEqual(manual["worst"]["id"], other["id"])

    def test_incident_counter_drop_then_recovery_does_not_create_reference(self):
        samples = session(count=2, loss_lap=2)
        for final in (4, 6):
            with self.subTest(final=final):
                changed = [replace(s, incidents=2 if 0.3 <= s.lap_dist_pct < 0.6 else
                                   final if s.lap_dist_pct >= 0.6 else 4)
                           if s.lap == 1 else s for s in samples]
                result = race_results(changed)
                row = next(row for row in result["laps"] if row["lap"] == 1)
                self.assertTrue(row["review_eligible"])
                self.assertIsNone(row["metrics"]["incidents_delta"])
                self.assertTrue(any("retrocede" in text for text in row["context"]))
                self.assertEqual(result["best"]["lap"], 2)

    def test_boundary_time_can_be_known_while_boundary_speed_stays_unknown(self):
        result = race_results(session(count=2, loss_lap=2, loss_segment=19))
        segment = result["segments"][0]
        self.assertEqual((segment["start_pct"], segment["end_pct"]), (95, 100))
        self.assertAlmostEqual(segment["delta_s"], 1)
        self.assertIsNone(segment["first"]["speed_exit_kmh"])
        self.assertIsNone(segment["second"]["speed_exit_kmh"])
        finish = result["comparison"]["points"][-1]
        self.assertEqual(finish["progress_pct"], 100)
        self.assertAlmostEqual(finish["delta_s"], 1)
        self.assertIsNone(finish["first"]["speed_mps"])

    def test_reported_damage_is_reviewable_but_not_reference(self):
        samples = [replace(s, damage="damaged") if s.lap == 1 else s
                   for s in session(count=2, loss_lap=2)]
        result = race_results(samples)
        self.assertEqual(result["best"]["lap"], 2)
        damaged = next(row for row in result["laps"] if row["lap"] == 1)
        self.assertTrue(damaged["review_eligible"])
        self.assertIn("Daño: con daño.", damaged["context"])
        manual = race_results(samples, damaged["id"])
        self.assertEqual(manual["status"], "ok")
        self.assertEqual(manual["worst"]["id"], damaged["id"])

    def test_lap_label_uses_majority_when_sdk_counter_lags_at_finish_line(self):
        samples = session(count=2, loss_lap=2)
        samples = [replace(s, lap=s.lap - 1) if i % 200 < 3 else s
                   for i, s in enumerate(samples)]
        result = race_results(samples)
        self.assertEqual(result["best"]["lap"], 1)
        self.assertEqual(result["worst"]["lap"], 2)


if __name__ == "__main__":
    unittest.main()
