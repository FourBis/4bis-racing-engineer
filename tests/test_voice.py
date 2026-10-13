import unittest
from types import SimpleNamespace
from unittest.mock import patch

from racing_engineer.voice import VoiceCoach, WindowsSpeech


NUMERIC_CUE = "Curva uno. Referencia: pico 80 por ciento; inicio entre 260 y 280 metros desde meta."


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeSpeaker:
    def __init__(self, *, error=None):
        self.available = error is None
        self.speaking = False
        self.error = error
        self.messages = []
        self.purges = 0
        self.closed = False

    def speak(self, text):
        if self.error:
            return False
        if self.speaking:
            return False
        self.messages.append(text)
        return True

    def purge(self):
        self.purges += 1
        self.speaking = False

    def close(self):
        self.closed = True
        self.purge()


def sample(time_s, **changes):
    values = {
        "source": "iracing", "session_id": "session-a", "time_s": time_s,
        "lap": 1, "speed_mps": 20.0, "brake": 0.0, "throttle": 0.0,
        "brake_raw": 0.0, "throttle_raw": 0.0, "steering_wheel_rad": 0.0,
        "gear": 3, "on_track": True, "is_replay": False,
        "abs_active": False, "vert_accel_mps2": 9.81,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class VoiceCoachTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.speaker = FakeSpeaker()
        self.coach = VoiceCoach("live", self.speaker, self.clock)

    def tearDown(self):
        self.coach.close()

    def enable(self):
        self.assertTrue(self.coach.set_enabled(True))
        self.assertEqual(1, len(self.speaker.messages))
        self.clock.advance(13)

    def test_opt_in_and_explicit_test_work_without_enabling_coach(self):
        self.assertFalse(self.coach.snapshot()["enabled"])
        self.assertEqual([], self.speaker.messages)
        self.assertTrue(self.coach.test())
        self.assertIn("Prueba de voz", self.speaker.messages[-1])
        self.assertFalse(self.coach.snapshot()["enabled"])
        self.assertIn("Voz local lista para probar", self.coach.snapshot()["status"])

    def test_attempt_events_distinguish_acceptance_and_suppression_reasons(self):
        self.coach.recording_id = "recording-a"
        self.assertTrue(self.coach.test())
        accepted = self.coach.drain_events()
        self.assertEqual(1, len(accepted))
        self.assertEqual(("voice", "test", "accepted", "recording-a"),
                         (accepted[0]["kind"], accepted[0]["cue"], accepted[0]["stage"],
                          accepted[0]["recording_id"]))
        self.assertIsNone(accepted[0]["session_id"])
        self.assertIsNone(accepted[0]["time_s"])
        self.assertTrue(accepted[0]["event_id"])
        self.assertTrue(accepted[0]["utc"])

        self.assertFalse(self.coach.test())  # global and type cooldown; global wins
        self.assertEqual("global_cooldown", self.coach.drain_events()[0]["reason"])
        self.clock.advance(13)
        self.assertFalse(self.coach.test())
        self.assertEqual("type_cooldown", self.coach.drain_events()[0]["reason"])
        self.clock.advance(23)
        self.speaker.speaking = True
        self.assertFalse(self.coach.test())
        self.assertEqual("busy", self.coach.drain_events()[0]["reason"])

    def test_event_captures_current_sample_and_abs_suppression_is_deduped_per_episode(self):
        self.enable()
        self.coach.drain_events()
        self.coach.recording_id = "recording-1"
        for index in range(5):
            self.coach.update(sample(1 + index * 0.1, lap=3, lap_dist_pct=0.42,
                                     brake_raw=0.5, abs_active=True), True)
        events = self.coach.drain_events()
        accepted = [event for event in events if event["stage"] == "accepted"]
        self.assertEqual(1, len(accepted))
        event = accepted[0]
        self.assertEqual(("session-a", 1.4, 3, 0.42),
                         (event["session_id"], event["time_s"], event["lap"], event["lap_dist_pct"]))

        # New sustained episode while global cooldown remains active: one logged reason only.
        self.coach.update(sample(1.5, abs_active=False), True)
        for index in range(5):
            self.coach.update(sample(1.6 + index * 0.1, lap_dist_pct=0.5, brake_raw=0.5,
                                     abs_active=True), True)
        rejected = [event for event in self.coach.drain_events()
                    if event["cue"] == "abs" and event["stage"] == "suppressed"]
        self.assertEqual(1, len(rejected))
        self.assertEqual("global_cooldown", rejected[0]["reason"])
        self.clock.advance(36)
        self.coach.update(sample(2.1, brake_raw=0.5, abs_active=True), True)
        self.assertEqual("ABS sostenido. Suaviza el freno.", self.speaker.messages[-1])

    def test_backend_failure_is_suppressed_and_fake_speaker_has_no_lifecycle_claim(self):
        self.assertTrue(self.coach.test())
        events = self.coach.drain_events()
        self.assertEqual(["accepted"], [event["stage"] for event in events])

        broken = FakeSpeaker(error="SAPI unavailable")
        coach = VoiceCoach("live", broken, self.clock)
        self.assertFalse(coach.test())
        event = coach.drain_events()[0]
        self.assertEqual(("suppressed", "backend_error"), (event["stage"], event["reason"]))
        coach.close()

    def test_native_lifecycle_keeps_original_recording_and_purge_marks_queued_id_cancelled(self):
        backend = WindowsSpeech()
        coach = VoiceCoach("live", backend, self.clock)
        coach.recording_id = "old-recording"
        with patch.object(WindowsSpeech, "speak", return_value=True):
            self.assertTrue(coach.test())
        attempt = coach.drain_events()[0]
        coach.recording_id = "new-recording"
        backend._emit(attempt["event_id"], "started")
        lifecycle = coach.drain_events()[0]
        self.assertEqual((attempt["event_id"], "old-recording", "started"),
                         (lifecycle["event_id"], lifecycle["recording_id"], lifecycle["stage"]))

        queued_id = "queued-event"
        with backend._lock:
            backend._queue.put_nowait(("speak", "texto", queued_id, backend._generation, None))
            backend._queued_ids.add(queued_id)
        backend.purge()
        cancelled = backend.drain_events()
        self.assertEqual([queued_id], [event["event_id"] for event in cancelled])
        self.assertEqual("cancelled", cancelled[0]["stage"])
        self.assertEqual([], backend.drain_events())
        coach.close()

    def test_sapi_status_failure_errors_active_cancels_queued_and_clears_speaking(self):
        class BrokenStatusVoice:
            def __init__(self):
                self.purges = 0

            @property
            def Status(self):
                raise RuntimeError("SAPI status failed")

            def Speak(self, text, flags):
                if flags == 2:
                    self.purges += 1

        backend = WindowsSpeech()
        backend.available = True
        backend.speaking = True
        backend._active_id = "active-event"
        backend._active_generation = backend._generation
        backend._started = True
        backend._speech_deadline = 1000
        backend._queued_ids.add("queued-event")
        backend._queue.put_nowait(("speak", "texto", "queued-event", backend._generation, None))

        self.assertFalse(backend._poll_status(BrokenStatusVoice()))
        events = backend.drain_events()
        self.assertEqual({("active-event", "error", "backend_error"),
                          ("queued-event", "cancelled", "backend_error")},
                         {(event["event_id"], event["stage"], event["reason"]) for event in events})
        self.assertFalse(backend.speaking)
        self.assertFalse(backend.available)
        self.assertIn("SAPI status failed", backend.error)

    def test_navigation_start_deadline_expires_during_lazy_backend_start(self):
        backend = WindowsSpeech()
        coach = VoiceCoach("live", backend, self.clock)
        coach._sample_context.update(session_id="reference-session", time_s=42)

        def delayed_start():
            self.clock.advance(1)
            return True

        with patch.object(backend, "_start", side_effect=delayed_start), \
                patch("racing_engineer.voice.time.monotonic", self.clock):
            self.assertFalse(coach._say("navigation_1", NUMERIC_CUE))
        events = coach.drain_events()
        self.assertEqual([("cancelled", "start_deadline")],
                         [(event["stage"], event["reason"]) for event in events])
        self.assertEqual(("reference-session", 42, "navigation_1"),
                         (events[0]["session_id"], events[0]["time_s"], events[0]["cue"]))
        self.assertTrue(backend._queue.empty())
        self.assertFalse(backend.speaking)
        self.assertEqual({}, coach._native_metadata)
        coach.close()

    def test_navigation_queued_command_expires_before_sapi_receives_text(self):
        backend = WindowsSpeech()
        calls = []
        voice = SimpleNamespace(Status=SimpleNamespace(RunningState=1),
                                Speak=lambda text, flags: calls.append((text, flags)))
        with patch.object(backend, "_start", return_value=True), \
                patch("racing_engineer.voice.time.monotonic", self.clock):
            self.assertTrue(backend.speak(NUMERIC_CUE, "expired", start_deadline=.5))
            command = backend._queue.get_nowait()
            self.clock.advance(.6)
            backend._start_command(voice, *command[1:])
        self.assertEqual([], calls)
        self.assertEqual([("expired", "cancelled", "start_deadline")],
                         [(e["event_id"], e["stage"], e["reason"]) for e in backend.drain_events()])
        self.assertFalse(backend.speaking)
        self.assertEqual(set(), backend._queued_ids)
        backend.close()

    def test_navigation_unconfirmed_start_is_cancelled_but_timely_started_audio_can_finish(self):
        for start_in_time in (False, True):
            with self.subTest(start_in_time=start_in_time):
                clock, backend, calls = FakeClock(), WindowsSpeech(), []
                voice = SimpleNamespace(Status=SimpleNamespace(RunningState=1),
                                        Speak=lambda text, flags: calls.append((text, flags)))
                with patch.object(backend, "_start", return_value=True), \
                        patch("racing_engineer.voice.time.monotonic", clock):
                    self.assertTrue(backend.speak(NUMERIC_CUE, "event", start_deadline=.5))
                    backend._start_command(voice, *backend._queue.get_nowait()[1:])
                    if start_in_time:
                        clock.advance(.1)
                        voice.Status.RunningState = 2
                        self.assertTrue(backend._poll_status(voice))
                    clock.advance(1)
                    voice.Status.RunningState = 1
                    self.assertTrue(backend._poll_status(voice))
                stages = [e["stage"] for e in backend.drain_events()]
                self.assertEqual(["started", "finished"] if start_in_time else ["cancelled"], stages)
                self.assertEqual([(NUMERIC_CUE, 17)] if start_in_time else [(NUMERIC_CUE, 17), ("", 2)], calls)
                self.assertFalse(backend.speaking)
                backend.close()

    def test_demo_replay_and_non_fresh_samples_never_trigger_driving_advice(self):
        for mode in ("demo", "replay"):
            speaker = FakeSpeaker()
            coach = VoiceCoach(mode, speaker, self.clock)
            coach.set_enabled(True)
            coach.update(sample(1, abs_active=True, brake_raw=0.8), fresh=True)
            self.assertEqual(1, len(speaker.messages))  # explicit test phrase only
            coach.close()
        self.enable()
        self.coach.update(sample(1, abs_active=True, brake_raw=0.8), fresh=False)
        self.assertEqual(1, len(self.speaker.messages))

    def navigation(self, time_s, **changes):
        return {"voice_ready": True, "session_id": "session-a", "sample_time_s": time_s,
                "lap_token": "session-a:lap-1", "next": {
                    "id": 1, "seconds_ahead": 9, "seconds_to_braking_start": 8.8,
                    "corner_name": "Curva 1", "numeric_guidance_ready": True,
                    "occurrence_token": "run-1:lap-1:zone-1",
                    "location_basis": "official_map_estimate",
                    "cue": NUMERIC_CUE},
                **changes}

    def test_navigation_once_per_lap_with_anticipation_and_current_identity(self):
        self.enable()
        for i in range(20):
            t = i / 10
            self.coach.update(sample(t), True, self.navigation(t))
        self.assertEqual(NUMERIC_CUE, self.speaker.messages[-1])
        self.assertEqual(2, len(self.speaker.messages))
        self.clock.advance(40)
        self.coach.update(sample(2), True, self.navigation(2))
        self.assertEqual(2, len(self.speaker.messages))
        self.coach.update(sample(2.1), True, self.navigation(2.1, lap_token="session-a:lap-2"))
        self.assertEqual(2, len(self.speaker.messages))  # Same destination through the finish line.
        nav = self.navigation(2.2, lap_token="session-a:lap-2")
        nav["next"]["occurrence_token"] = "run-1:lap-2:zone-1"
        self.coach.update(sample(2.2), True, nav)
        self.assertEqual(3, len(self.speaker.messages))

    def test_navigation_busy_attempt_expires_and_does_not_repeat(self):
        self.enable()
        self.speaker.speaking = True
        for i in range(12):
            t = i / 10
            self.coach.update(sample(t), True, self.navigation(t))
        attempts = [e for e in self.coach.drain_events() if e["cue"].startswith("navigation_")]
        self.assertEqual([("suppressed", "busy")], [(e["stage"], e["reason"]) for e in attempts])
        self.speaker.speaking = False
        self.coach.update(sample(1.2), True, self.navigation(1.2))
        self.assertEqual(1, len(self.speaker.messages))

    def test_navigation_requires_early_window_and_time_to_finish_the_fixed_anchor(self):
        cases = ({"seconds_to_braking_start": 7.9}, {"seconds_to_braking_start": 10.1},
                 {"seconds_to_braking_start": 3}, {"seconds_to_braking_start": float("nan")},
                 {"cue": NUMERIC_CUE * 3}, {"numeric_guidance_ready": False})
        for change in cases:
            with self.subTest(change=change):
                speaker = FakeSpeaker()
                coach = VoiceCoach("live", speaker, self.clock)
                coach.enabled = True
                for i in range(11):
                    nav = self.navigation(i / 10)
                    nav["next"].update(change)
                    coach.update(sample(i / 10), True, nav)
                self.assertEqual([], speaker.messages)
                coach.close()

    def test_navigation_window_uses_earliest_start_instead_of_later_threshold(self):
        speaker = FakeSpeaker()
        coach = VoiceCoach("live", speaker, self.clock)
        coach.enabled = True
        for i in range(11):
            nav = self.navigation(i / 10)
            nav["next"].update(seconds_ahead=10.5, seconds_to_braking_start=9.5)
            coach.update(sample(i / 10), True, nav)
        self.assertEqual([NUMERIC_CUE], speaker.messages)
        coach.close()

    def test_navigation_cancels_on_incompatible_context_or_braking_and_does_not_retry(self):
        changes = ("numeric", "not_ready", "session", "identity", "new_reference", "at_start",
                   "braking", "stale", "elapsed")
        for change in changes:
            with self.subTest(change=change):
                clock, speaker = FakeClock(), FakeSpeaker()
                coach = VoiceCoach("live", speaker, clock)
                coach.enabled = True
                for i in range(11):
                    coach.update(sample(i / 10), True, self.navigation(i / 10))
                self.assertEqual([NUMERIC_CUE], speaker.messages)
                speaker.speaking = True
                nav, current, fresh = self.navigation(1.1), sample(1.1), True
                if change == "numeric":
                    nav["next"]["numeric_guidance_ready"] = False
                elif change == "not_ready":
                    nav["voice_ready"] = False
                elif change == "session":
                    nav["session_id"] = "other"
                elif change == "identity":
                    nav["next"]["occurrence_token"] = "new-run:lap-1:zone-1"
                elif change == "new_reference":
                    nav["next"]["cue"] = NUMERIC_CUE.replace("80", "65")
                elif change == "at_start":
                    nav["next"]["seconds_to_braking_start"] = 0
                elif change == "braking":
                    current = sample(1.1, brake=.1)
                elif change == "stale":
                    fresh = False
                elif change == "elapsed":
                    clock.advance(9)
                coach.update(current, fresh, nav)
                self.assertEqual(1, speaker.purges)
                self.assertIsNone(coach._active_navigation)
                self.assertEqual([NUMERIC_CUE], speaker.messages)
                coach.update(sample(1.2), True, self.navigation(1.2))
                self.assertEqual([NUMERIC_CUE], speaker.messages)
                coach.close()

    def test_navigation_never_speaks_anonymous_or_unmapped_points(self):
        self.enable()
        for i in range(11):
            self.coach.update(sample(i / 10), True)
        for i, field in enumerate(("corner_name", "cue", "location_basis", "numeric_guidance_ready",
                                   "occurrence_token", "seconds_to_braking_start")):
            t = 1.1 + i / 10
            nav = self.navigation(t)
            nav["next"].pop(field)
            self.coach.update(sample(t), True, nav)
        self.assertEqual(1, len(self.speaker.messages))

    def test_abs_has_priority_over_navigation_even_during_its_cooldown(self):
        self.enable()
        for i in range(11):
            self.coach.update(sample(i / 10), True, self.navigation(i / 10))
        self.assertEqual(NUMERIC_CUE, self.speaker.messages[-1])
        self.speaker.speaking = True
        self.clock.advance(1)
        for i in range(5):
            self.coach.update(sample(1.1 + i / 10, brake=0.5, brake_raw=0.5, abs_active=True), True)
        self.assertEqual("ABS sostenido. Suaviza el freno.", self.speaker.messages[-1])
        self.assertEqual(1, self.speaker.purges)

    def test_navigation_rejects_stale_context_missing_metadata_late_cue_and_abs(self):
        self.enable()
        for i in range(11):
            self.coach.update(sample(i / 10), True)
        contexts = [self.navigation(1.1, session_id="other"),
                    self.navigation(0), self.navigation(1.3, voice_ready=False),
                    self.navigation(1.4, next={"id": 1, "seconds_ahead": 2}),
                    self.navigation(1.5, next={"id": 1, "seconds_ahead": None}),
                    self.navigation(1.6, next={"id": 4, "seconds_ahead": 5}),
                    self.navigation(1.7)]
        for i, nav in enumerate(contexts):
            self.coach.update(sample(1.1 + i / 10, abs_active=i == 6), True, nav)
        self.assertEqual(1, len(self.speaker.messages))
        self.coach.update(sample(1.8), False, self.navigation(1.8))
        self.assertEqual(1, len(self.speaker.messages))
        for mode in ("demo", "replay"):
            coach = VoiceCoach(mode, self.speaker, self.clock)
            coach.enabled = True
            for i in range(20):
                coach.update(sample(i / 10), True, self.navigation(i / 10))
            coach.close()
        self.assertEqual(1, len(self.speaker.messages))

    def test_abs_requires_abs_flag_braking_speed_and_sustained_duration(self):
        self.enable()
        for index in range(4):
            self.coach.update(sample(index * 0.1, brake_raw=0.5, abs_active=False), True)
        self.assertEqual(1, len(self.speaker.messages))  # pedal input alone is not ABS
        for index in range(5):
            self.coach.update(sample(1 + index * 0.1, brake_raw=0.5, abs_active=True), True)
        self.assertEqual("ABS sostenido. Suaviza el freno.", self.speaker.messages[-1])

    def test_raw_throttle_hint_requires_vertical_and_stable_positive_gear(self):
        self.enable()
        self.coach.update(sample(1, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(1.2, throttle_raw=0.6, steering_wheel_rad=0.5), True)
        self.assertEqual("Gas brusco. Aplica progresivo.", self.speaker.messages[-1])

        # A single raw spike on a kerb/out-of-range vertical sample is ignored.
        self.coach.update(sample(2, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(2.2, throttle_raw=0.8, steering_wheel_rad=0.5,
                                 vert_accel_mps2=16.0), True)
        self.assertEqual(2, len(self.speaker.messages))

        # A gear change or missing raw/vertical channel also suppresses the cue.
        self.coach.update(sample(3, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(3.2, throttle_raw=0.8, steering_wheel_rad=0.5, gear=4), True)
        self.coach.update(sample(4, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(4.2, throttle_raw=None, steering_wheel_rad=0.5), True)
        self.coach.update(sample(5, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(5.2, throttle_raw=0.8, steering_wheel_rad=0.5,
                                 vert_accel_mps2=None), True)
        self.assertEqual(2, len(self.speaker.messages))

    def test_gaps_clear_raw_history_and_disconnect_purges_once(self):
        self.enable()
        self.coach.update(sample(1, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(1.6, throttle_raw=0.8, steering_wheel_rad=0.5), True)
        self.assertEqual(1, len(self.speaker.messages))
        before_disconnect = self.speaker.purges
        self.coach.update(None, fresh=False)
        self.coach.update(None, fresh=False)
        self.assertEqual(before_disconnect + 1, self.speaker.purges)
        self.assertEqual(1, len(self.speaker.messages))

    def test_busy_speaker_drops_a_one_tick_event_without_old_queue(self):
        self.enable()
        self.coach.update(sample(1, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.speaker.speaking = True
        self.coach.update(sample(1.2, throttle_raw=0.8, steering_wheel_rad=0.5), True)
        self.speaker.speaking = False
        self.coach.update(sample(1.3, throttle_raw=0.8, steering_wheel_rad=0.5), True)
        self.assertEqual(1, len(self.speaker.messages))

    def test_global_and_per_type_cooldowns_suppress_repeats(self):
        self.enable()
        self.coach.update(sample(1, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(1.2, throttle_raw=0.6, steering_wheel_rad=0.5), True)
        self.assertEqual("Gas brusco. Aplica progresivo.", self.speaker.messages[-1])
        self.clock.advance(13)
        self.coach.update(sample(1.3, throttle_raw=0.0, steering_wheel_rad=0.5), True)
        self.coach.update(sample(1.5, throttle_raw=0.6, steering_wheel_rad=0.5), True)
        self.assertEqual(2, len(self.speaker.messages))  # same cue type <35 s

    def test_windows_backend_is_lazy_until_a_real_or_explicit_speech(self):
        backend = WindowsSpeech()
        self.assertIsNone(backend._thread)
        backend.close()
        self.assertIsNone(backend._thread)

    def test_heartbeat_requires_stable_zone_and_lap_message_is_limited(self):
        self.enable()
        self.clock.advance(47)
        for t in (1.0, 1.5, 2.0, 2.1):
            self.coach.update(sample(t, speed_mps=20, brake=0.0,
                                     steering_wheel_rad=0.0), True)
        self.assertEqual("Telemetría activa.", self.speaker.messages[-1])
        self.clock.advance(13)
        self.coach.update(sample(2.2, lap=2), True)
        self.assertEqual("Vuelta 2. Telemetría activa.", self.speaker.messages[-1])

    def test_disable_purges_and_speaker_error_is_visible(self):
        self.enable()
        self.assertTrue(self.coach.set_enabled(False))
        self.assertEqual(1, self.speaker.purges)
        broken = FakeSpeaker(error="No se encontró Helena")
        coach = VoiceCoach("live", broken, self.clock)
        self.assertFalse(coach.set_enabled(True))
        self.assertIn("No se encontró Helena", coach.snapshot()["status"])
        coach.close()
        self.assertTrue(broken.closed)


if __name__ == "__main__":
    unittest.main()
