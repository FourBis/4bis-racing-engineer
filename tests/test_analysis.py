import unittest
from dataclasses import replace

from racing_engineer.analysis import compare_laps, report_markdown, summarize
from racing_engineer.model import Sample


def lap_samples(lap: int, *, session: str = "s1", source: str = "demo",
                speed: float = 20.0, damage: str = "healthy", traffic: str = "clear",
                replay: bool | None = False, on_track: bool | None = True,
                incidents: int | None = 0) -> list[Sample]:
    base = (lap - 1) * 2.3
    samples = [
        Sample(
            source=source, session_id=session, time_s=base + i / 10,
            lap=lap, lap_dist_pct=i / 20, speed_mps=speed + i,
            brake=0.0, throttle=0.8, steering_wheel_rad=0.0,
            damage=damage, traffic=traffic, is_replay=replay,
            on_track=on_track, incidents=incidents,
        )
        for i in range(21)
    ]
    # This p=0 sample is the observed finish crossing; lap counter can lag it.
    samples.append(replace(samples[-1], time_s=base + 2.1, lap=lap + 1, lap_dist_pct=0.0))
    return samples


class AnalysisTests(unittest.TestCase):
    def test_incident_first_observed_at_finish_excludes_the_completed_lap(self):
        samples = lap_samples(1)
        samples[-1] = replace(samples[-1], incidents=1)
        previous = summarize(samples)["laps"][0]
        self.assertTrue(previous["complete"])
        self.assertFalse(previous["eligible"])
        self.assertIn("incidente", previous["reason"])

    def test_sustained_brake_rise_is_driving_cue_not_sensor_diagnosis(self):
        samples = lap_samples(1)
        samples[5] = replace(samples[5], brake=0.0)
        samples[6] = replace(samples[6], brake=0.8)
        samples[7] = replace(samples[7], brake=0.8)

        findings = summarize(samples)["findings"]
        self.assertIn("brake_application", [item["category"] for item in findings])
        self.assertNotIn("pedal_signal_candidate", [item["category"] for item in findings])
        self.assertTrue(any("no demuestra falla" in item["interpretation"] for item in findings))

    def test_brake_release_flags_reapplication_but_not_monotone_release(self):
        samples = lap_samples(1)
        for i, value in zip((6, 7, 8), (0.8, 0.4, 0.6)):
            samples[i] = replace(samples[i], brake=value)
        findings = summarize(samples)["findings"]
        release = next(item for item in findings if item["category"] == "brake_release")
        self.assertIn("puede ser una corrección", release["interpretation"])

        monotone = lap_samples(1)
        for i, value in zip((6, 7, 8), (0.8, 0.6, 0.4)):
            monotone[i] = replace(monotone[i], brake=value)
        self.assertNotIn("brake_release", [item["category"] for item in summarize(monotone)["findings"]])

    def test_repeated_isolated_pulses_are_only_low_confidence_candidate(self):
        samples = lap_samples(1, damage="unknown", traffic="unknown")
        for index in (5, 12):
            samples[index] = replace(samples[index], brake=0.45)

        findings = summarize(samples)["findings"]
        pulse = next(item for item in findings if item["category"] == "pedal_signal_candidate")
        self.assertEqual(pulse["confidence"], "baja")

    def test_unknown_damage_and_contact_exclude_comparison(self):
        unknown = lap_samples(1, damage="unknown")
        clean = lap_samples(2)
        summary = summarize(unknown + clean)
        self.assertFalse(summary["laps"][0]["eligible"])
        self.assertTrue(any("daño desconocido" in line for line in report_markdown(summary).splitlines()))
        lap_ids = [lap["id"] for lap in summary["laps"]]
        comparison = compare_laps(unknown + clean, lap_ids[0], lap_ids[1])
        self.assertEqual(comparison["status"], "ineligible")

        contacted = lap_samples(3, incidents=1)
        self.assertIn("contacto", summarize(contacted)["laps"][0]["reason"])

    def test_partial_lap_and_nullable_controls_stay_partial(self):
        partial = lap_samples(1)[:10]
        event_partial = partial.copy()
        event_partial[6] = replace(event_partial[6], brake=0.8)
        event_partial[7] = replace(event_partial[7], brake=0.8)
        self.assertIn("brake_application", [item["category"] for item in summarize(event_partial)["findings"]])

        partial = [replace(sample, brake=None, throttle=None, speed_mps=None) for sample in partial]
        summary = summarize(partial)
        self.assertFalse(summary["laps"][0]["complete"])
        self.assertFalse(summary["laps"][0]["eligible"])
        self.assertEqual(summary["findings"], [])

    def test_range_without_observed_finish_is_partial(self):
        full_range_no_crossing = lap_samples(1)[:-1]
        summary = summarize(full_range_no_crossing)
        self.assertFalse(summary["laps"][0]["complete"])
        self.assertEqual(summary["laps"][0]["reason"], "cruce final no observado")
        self.assertAlmostEqual(summary["laps"][0]["duration_s"], 2.0)
        start_not_observed = summarize(lap_samples(1)[2:-1])
        self.assertEqual(start_not_observed["laps"][0]["reason"], "inicio de vuelta no observado en meta")

    def test_start_at_two_percent_is_not_a_line_boundary_and_crossing_time_is_interpolated(self):
        progress = [0.02, 0.5, 0.99, 0.0, 0.03, 0.5, 0.99, 0.0]
        samples = [Sample(
            source="demo", session_id="boundary", time_s=i * 0.1,
            lap_dist_pct=p, lap=4, damage="healthy", traffic="clear",
            is_replay=False, on_track=True, incidents=0,
        ) for i, p in enumerate(progress)]
        laps = summarize(samples)["laps"]
        self.assertFalse(laps[0]["complete"])
        self.assertTrue(laps[1]["complete"])

        dense = [Sample(
            source="demo", session_id="crossing", time_s=p * 10,
            lap_dist_pct=p, lap=7, damage="healthy", traffic="clear",
            is_replay=False, on_track=True, incidents=0,
        ) for p in (i / 100 for i in range(99))]
        dense.append(replace(dense[-1], time_s=10.2, lap_dist_pct=0.02))
        self.assertAlmostEqual(summarize(dense)["laps"][0]["duration_s"], 10.0)

    def test_distance_interpolation_and_session_gate(self):
        first, second = lap_samples(1, speed=20), lap_samples(2, speed=24)
        # Offset a sample in the second lap to prove values are matched by distance.
        second[10] = replace(second[10], time_s=second[10].time_s + 0.02)
        samples = first + second
        ids = [lap["id"] for lap in summarize(samples)["laps"]]
        result = compare_laps(samples, ids[0], ids[1])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(compare_laps(samples, ids[0], ids[0])["status"], "same_lap")
        midpoint = next(point for point in result["points"] if point["progress_pct"] == 50)
        self.assertAlmostEqual(midpoint["second"]["speed_mps"] - midpoint["first"]["speed_mps"], 4)
        self.assertEqual(midpoint["delta_speed_mps"], 4)
        self.assertEqual(result["points"][0]["delta_s"], 0)
        self.assertAlmostEqual(summarize(first)["laps"][0]["duration_s"], 2.0)

        # An explicitly absent input remains absent at that distance.
        second[10] = replace(second[10], speed_mps=None)
        with_hole = compare_laps(first + second, ids[0], ids[1])
        middle = next(point for point in with_hole["points"] if point["progress_pct"] == 50)
        self.assertIsNone(middle["second"]["speed_mps"])

        other_session = lap_samples(3, session="s2")
        third_id = summarize(other_session)["laps"][0]["id"]
        self.assertEqual(compare_laps(samples + other_session, ids[0], third_id)["status"], "different_session")

    def test_progress_wrap_and_clock_reset_split_laps(self):
        wrapped = []
        progresses = [i / 20 for i in range(21)] + [i / 20 for i in range(21)] + [0.0]
        for i, progress in enumerate(progresses):
            wrapped.append(Sample(
                source="demo", session_id="wrap", time_s=i / 10,
                lap=7, lap_dist_pct=progress, speed_mps=20.0, damage="healthy", traffic="clear",
                is_replay=False, on_track=True, incidents=0,
            ))
        wraps = summarize(wrapped)["laps"]
        self.assertEqual(len(wraps), 3)
        self.assertTrue(all(lap["complete"] for lap in wraps[:2]))
        self.assertFalse(wraps[-1]["complete"])

        before_reset = lap_samples(1, session="reset")
        after_reset = [replace(sample, time_s=i / 10)
                       for i, sample in enumerate(lap_samples(2, session="reset"))]
        reset_laps = summarize(before_reset + after_reset)["laps"]
        complete = [lap for lap in reset_laps if lap["complete"]]
        self.assertEqual(len(complete), 2)
        self.assertEqual(compare_laps(before_reset + after_reset, complete[0]["id"], complete[1]["id"])["status"], "different_run")

    def test_straight_candidate_uses_inputs_and_report_discloses_mode_and_status(self):
        samples = lap_samples(1)
        for i in range(1, 13):
            samples[i] = replace(samples[i], throttle=0.97, brake=0.0, steering_wheel_rad=0.02,
                                 rpm=4000 + i * 100, gear=4)
        summary = summarize(samples)
        candidate = next(item for item in summary["findings"] if item["category"] == "straight_candidate")
        self.assertEqual(candidate["confidence"], "baja")
        second = lap_samples(2)
        for i in range(1, 13):
            second[i] = replace(second[i], throttle=0.97, brake=0.0, steering_wheel_rad=0.02,
                                rpm=4000 + i * 100, gear=4)
        combined = samples + second
        ids = [lap["id"] for lap in summarize(combined)["laps"] if lap["complete"]]
        comparison = compare_laps(combined, ids[0], ids[1])
        self.assertTrue(comparison["straight_sections"])
        section = comparison["straight_sections"][0]
        self.assertIn("first_rpm", section["entry"])
        self.assertIn("first_gear", section["exit"])
        self.assertIn("first", section["speed_change_mps"])
        self.assertIn("rebufo", comparison["limitations"])

        report = report_markdown(summary)
        self.assertIn("SINTÉTICA", report)
        self.assertIn("Muestras analizadas/recibidas:", report)
        self.assertIn("Ventana de memoria truncada:", report)
        self.assertIn("Ventana de memoria truncada: no", report)
        self.assertIn("Vueltas parciales: 1", report)

        app_report = {
            **summary,
            "source_mode": "replay", "synthetic": True,
            "samples_received": 500, "samples_analyzed": 120,
            "memory_window_truncated": True, "operator_annotations": ["contacto reportado"],
        }
        rendered = report_markdown(app_report)
        self.assertIn("REPLAY SINTÉTICO", rendered)
        self.assertIn("120/500", rendered)
        self.assertIn("Ventana de memoria truncada: sí", rendered)
        self.assertIn("Vueltas parciales:", rendered)
        self.assertIn("contacto reportado", rendered)

        below_threshold = [replace(s, throttle=0.94) for s in samples]
        self.assertNotIn("straight_candidate", [x["category"] for x in summarize(below_threshold)["findings"]])

        with_hole = samples.copy()
        with_hole[6] = replace(with_hole[6], throttle=None)
        self.assertNotIn("straight_candidate", [x["category"] for x in summarize(with_hole)["findings"]])

    def test_gap_blocks_clean_comparison(self):
        first = lap_samples(1)
        for i in range(5, len(first)):
            first[i] = replace(first[i], time_s=first[i].time_s + 1.0)
        second = [replace(s, time_s=s.time_s + 1.0) for s in lap_samples(2)]
        combined = first + second
        laps = summarize(combined)["laps"]
        self.assertEqual(compare_laps(combined, laps[0]["id"], laps[1]["id"])["status"], "ineligible")


if __name__ == "__main__":
    unittest.main()
