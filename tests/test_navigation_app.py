import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from racing_engineer.app import App, collect
from racing_engineer.demo import demo_samples
from racing_engineer.voice import VoiceCoach


class NavigationAppTests(unittest.TestCase):
    def test_learned_zones_reach_voice_and_history_with_a_silent_synthetic_sdk(self):
        messages, clock = [], [0.0]
        speaker = SimpleNamespace(available=True, speaking=False, error=None,
                                  speak=lambda message: messages.append(message) or True,
                                  purge=lambda: None, close=lambda: None)
        with tempfile.TemporaryDirectory() as tmp:
            app = App("live", Path(tmp) / "unused.sqlite")
            app.voice = VoiceCoach("live", speaker=speaker, clock=lambda: clock[0])
            app.voice.enabled = True
            app.connected = True
            for row in demo_samples():
                progress = row.lap_dist_pct
                brake = .8 if any(start <= progress < end for start, end in
                                 ((.065, .095), (.445, .475), (.815, .845))) else 0.0
                sample = replace(row, source="iracing", time_s=row.time_s * 3,
                                 lap_time_s=row.lap_time_s * 3, speed_mps=4056.9 / 90,
                                 steering_wheel_rad=0, brake=brake, track_id=127,
                                 track_name="Road Atlanta", track_config="Full Course",
                                 track_length_m=4056.9, car_id=119, car_name="Synthetic SDK fixture")
                clock[0] = sample.time_s
                app.add(sample)
                app.voice.update(sample, fresh=app.fresh(), navigation=app.navigation())
                app.flush_voice_events()
            cues = [e for e in app.voice_history()["events"]
                    if e["stage"] == "accepted" and e["cue"].startswith("navigation_")]
            self.assertGreaterEqual(len(cues), 2)
            self.assertEqual(len(cues), len({(e["lap"], e["cue"]) for e in cues}))
            self.assertTrue(all(e["time_s"] >= 90 and not e["persisted"] for e in cues))
            self.assertTrue(all("Viene curva" in e["message"] and "suelta progresivo" in e["message"] for e in cues))
            self.assertEqual({"Curva 1", "Curva 6", "Curva 10a"},
                             {z["corner_name"] for z in app.navigation()["zones"]})
            app.voice.close()

    def test_demo_collection_exports_learned_reference_without_driving_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App("demo", Path(tmp) / "unused.sqlite")
            with patch.object(app.voice, "_say") as speech:
                app.voice.enabled = True
                collect(app, demo_samples(), threading.Event(), realtime=False)
                speech.assert_not_called()
            state = app.snapshot()
            self.assertIsNotNone(state["navigation"]["reference"])
            self.assertEqual(3, len(state["navigation"]["zones"]))
            self.assertEqual(state["navigation"], app.summary()["navigation"])
            self.assertFalse(Path(tmp, "unused.sqlite").exists())
            app.voice.close()

    def test_stale_live_keeps_reference_but_removes_distance_delta_and_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = App("live", Path(tmp) / "unused.sqlite")
            for sample in demo_samples():
                app.add(replace(sample, source="iracing"))
            app.connected = True
            self.assertIsNotNone(app.navigation()["reference"])
            app.last_received_at = -100
            stale = app.navigation()
            self.assertIsNone(stale["next"])
            self.assertIsNone(stale["delta_s"])
            self.assertFalse(stale["voice_ready"])
            self.assertIsNotNone(stale["reference"])
            app.voice.close()
