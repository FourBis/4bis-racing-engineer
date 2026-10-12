"""Local, opt-in voice hints for live iRacing telemetry."""

from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from importlib.util import find_spec


_LIMITATIONS = [
    "Avisos heurísticos basados en telemetría del simulador; no prueban presión física "
    "ni diagnóstico del auto.",
    "Sin señal suficiente o confiable, no se emite aviso.",
    "La voz local requiere Windows SAPI y una voz Helena en español instalada.",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WindowsSpeech:
    """SAPI en un hilo STA propio; conserva como máximo una frase pendiente."""

    def __init__(self) -> None:
        self.available = bool(os.name == "nt" and find_spec("pythoncom") and find_spec("win32com"))
        self.ready = False
        self.speaking = False
        self.error: str | None = None
        self._queue: queue.Queue[tuple[str, str, str, int] | None] = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._events_lock = threading.Lock()
        self._events: deque[dict] = deque()
        self._generation = 0
        self._active_id: str | None = None
        self._active_generation: int | None = None
        self._queued_ids: set[str] = set()
        self._started = False
        self._purge = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._speech_deadline = 0.0

    def _start(self) -> bool:
        if os.name != "nt":
            self.error = "SAPI de voz solo está disponible en Windows"
            return False
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self.error = None
                self._ready.clear()
                self._thread = threading.Thread(target=self._run, name="racing-voice-sapi", daemon=True)
                self._thread.start()
        if not self._ready.wait(3.0):
            self.error = "SAPI no respondió al iniciar"
            return False
        return self.available

    @staticmethod
    def _spanish_helena(voice) -> object:
        voices = voice.GetVoices()
        for index in range(voices.Count):
            token = voices.Item(index)
            name = token.GetDescription().casefold()
            try:
                language = str(token.GetAttribute("Language"))
                language_id = int(language, 16)
                is_spanish = language_id & 0x3FF == 0x0A
            except (TypeError, ValueError):
                is_spanish = "spanish" in name or "español" in name
            if "helena" in name and is_spanish:
                return token
        raise RuntimeError("No se encontró una voz SAPI local Helena en español")

    def _run(self) -> None:
        pythoncom = None
        voice = None
        initialized = False
        try:
            import pythoncom as com
            import win32com.client

            pythoncom = com
            pythoncom.CoInitialize()
            initialized = True
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Voice = self._spanish_helena(voice)
            self.available = True
            self.ready = True
        except Exception as exc:
            self.available = False
            self.error = f"No se pudo iniciar SAPI: {exc}"
            self._ready.set()
            if initialized:
                pythoncom.CoUninitialize()
            return

        self._ready.set()
        try:
            while True:
                if self._purge.is_set():
                    self._purge.clear()
                    voice.Speak("", 2)  # SVSFPurgeBeforeSpeak
                    with self._lock:
                        self.speaking = False
                try:
                    command = self._queue.get(timeout=0.05)
                except queue.Empty:
                    command = None
                if command is not None:
                    action, text, event_id, generation = command
                    if action == "close":
                        voice.Speak("", 2)
                        break
                    if action == "speak":
                        with self._lock:
                            current = generation == self._generation and event_id in self._queued_ids
                            self._queued_ids.discard(event_id)
                            if current:
                                self._active_id = event_id
                                self._active_generation = generation
                                self._started = False
                                if voice.Status.RunningState != 2:  # SPRST_IS_SPEAKING
                                    voice.Speak(text, 17)  # SVSFlagsAsync | SVSFPIsNotXML
                                else:
                                    self._emit(event_id, "error", "busy")
                                    self._active_id = None
                                    self.speaking = False
                if not self._poll_status(voice):
                    break
        except Exception as exc:
            self._fail_backend(voice, exc)
        finally:
            self.available = False
            self.speaking = False
            try:
                voice.Speak("", 2)
            except Exception:
                pass
            if initialized:
                pythoncom.CoUninitialize()

    def _poll_status(self, voice) -> bool:
        try:
            state = voice.Status.RunningState
        except Exception as exc:
            self._fail_backend(voice, exc)
            return False
        with self._lock:
            event_id = self._active_id
            generation = self._active_generation
            current = event_id is not None and generation == self._generation
            if current and state == 2 and not self._started:
                self._emit(event_id, "started")
                self._started = True
                self.speaking = True
            elif current and state != 2 and self._started:
                self._emit(event_id, "finished")
                self._active_id = None
                self._active_generation = None
                self._started = False
                self.speaking = False
            elif (current and not self._started
                  and time.monotonic() >= self._speech_deadline):
                self._emit(event_id, "error", "unconfirmed_timeout")
                self._active_id = None
                self._active_generation = None
                self.speaking = False
        return True

    def _fail_backend(self, voice, error: Exception) -> None:
        self.available = False
        self.error = f"Error de locución SAPI: {error}"
        with self._lock:
            self._generation += 1
            active_id = self._active_id
            queued_ids = set(self._queued_ids)
            self._active_id = None
            self._active_generation = None
            self._started = False
            self._queued_ids.clear()
            self.speaking = False
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        if active_id is not None:
            self._emit(active_id, "error", "backend_error", str(error))
        for event_id in queued_ids:
            self._emit(event_id, "cancelled", "backend_error")
        try:
            voice.Speak("", 2)  # cancel any SAPI request left in progress
        except Exception:
            pass

    def _emit(self, event_id: str, stage: str, reason: str | None = None,
              message: str | None = None) -> None:
        event = {"event_id": event_id, "kind": "voice", "stage": stage,
                 "reason": reason, "utc": _utc_now()}
        if message is not None:
            event["message"] = message
        with self._events_lock:
            self._events.append(event)

    def drain_events(self) -> list[dict]:
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        return events

    def speak(self, text: str, event_id: str | None = None) -> bool:
        if not self._start():
            return False
        event_id = event_id or str(uuid.uuid4())
        try:
            with self._lock:
                if self.speaking or not self._queue.empty():
                    return False
                generation = self._generation
                self._queue.put_nowait(("speak", text, event_id, generation))
                self._queued_ids.add(event_id)
                self.speaking = True
                self._speech_deadline = time.monotonic() + max(1.0, len(text) / 15)
            return True
        except queue.Full:
            return False

    def purge(self) -> None:
        with self._lock:
            self._generation += 1
            ids = set(self._queued_ids)
            self._queued_ids.clear()
            if self._active_id is not None:
                ids.add(self._active_id)
            self._active_id = None
            self._active_generation = None
            self._started = False
            self.speaking = False
            self._purge.set()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        for event_id in ids:
            self._emit(event_id, "cancelled", "purge")

    def close(self) -> None:
        self.purge()
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                self._queue.put_nowait(("close", "", "", self._generation))
            except queue.Full:
                pass
            thread.join(timeout=2.0)


class VoiceCoach:
    """Conservative voice hints; only live, fresh iRacing samples can coach."""

    def __init__(self, mode, speaker=None, clock=time.monotonic) -> None:
        self.mode = str(mode).lower()
        self._speaker = speaker if speaker is not None else WindowsSpeech()
        self._clock = clock
        self.enabled = False
        self.last_message: str | None = None
        self.status = "Voz desactivada"
        self._history: deque = deque(maxlen=12)
        self._session: str | None = None
        self._last_sample_time: float | None = None
        self._last_lap: int | None = None
        self._abs_since: float | None = None
        self._abs_announced = False
        self._stable_since: float | None = None
        self._last_global_speech: float | None = None
        self._last_type_speech: dict[str, float] = {}
        self._last_heartbeat: float | None = None
        self._last_message_kind: str | None = None
        self._had_live_stream = False
        self._silenced = True
        self.recording_id: str | None = None
        self._events: deque[dict] = deque()
        self._events_lock = threading.Lock()
        self._native_metadata: dict[str, dict] = {}
        self.last_sample = None
        self._sample_context: dict = {"session_id": None, "time_s": None, "lap": None,
                                      "lap_dist_pct": None}
        self._abs_logged_suppression: set[str] = set()
        self._navigation_lap = None
        self._navigation_attempted: set[int] = set()

    @property
    def _error(self) -> str | None:
        return getattr(self._speaker, "error", None)

    def _speaking(self) -> bool:
        return bool(getattr(self._speaker, "speaking", False))

    def _refresh_status(self) -> None:
        if self._error:
            self.status = f"Error de voz: {self._error}"
        elif not self.enabled and self._speaking() and self._last_message_kind in ("test", "enable"):
            self.status = "Prueba de voz en curso"
        elif not self.enabled and getattr(self._speaker, "available", False):
            self.status = "Voz local lista para probar; coach desactivado"
        elif not self.enabled:
            self.status = "Voz desactivada"
        elif self._speaking():
            self.status = "Locución en curso"
        elif getattr(self._speaker, "available", False):
            self.status = "Voz local activa"
        else:
            self.status = "Voz local no disponible"

    def _clear_history(self, purge: bool = False) -> None:
        self._history.clear()
        self._session = None
        self._last_sample_time = None
        self._last_lap = None
        self._abs_since = None
        self._abs_announced = False
        self._stable_since = None
        self._abs_logged_suppression.clear()
        if purge:
            self._speaker.purge()

    def _enqueue_event(self, event: dict) -> None:
        with self._events_lock:
            self._events.append(event)

    def drain_events(self) -> list[dict]:
        with self._events_lock:
            events = list(self._events)
            self._events.clear()
        # Only the native backend exposes lifecycle events; legacy fakes remain speak(text)-only.
        drain_native = getattr(self._speaker, "drain_events", None)
        native_events = drain_native() if callable(drain_native) else []
        for event in native_events:
            metadata = self._native_metadata.get(event.get("event_id"), {})
            merged = {**metadata, **event}
            events.append(merged)
            if event.get("stage") in ("finished", "cancelled", "error"):
                self._native_metadata.pop(event.get("event_id"), None)
        return sorted(events, key=lambda event: event.get("utc", ""))

    def _say(self, kind: str, message: str) -> bool:
        now = self._clock()
        last_kind = self._last_type_speech.get(kind)
        urgent_after_navigation = (
            kind in ("abs", "gas") and (self._last_message_kind or "").startswith("navigation_")
            and (last_kind is None or now - last_kind >= 35)
        )
        if urgent_after_navigation and self._speaking():
            self._speaker.purge()
        event_id = str(uuid.uuid4())
        base = {
            "event_id": event_id, "kind": "voice", "cue": kind, "message": message,
            "utc": _utc_now(), "mode": self.mode, "recording_id": self.recording_id,
            **self._sample_context,
        }
        reason = None
        if self._speaking():
            reason = "busy"
        elif (not urgent_after_navigation and self._last_global_speech is not None
              and now - self._last_global_speech < 12):
            reason = "global_cooldown"
        else:
            if last_kind is not None and now - last_kind < 35:
                reason = "type_cooldown"
        if reason is not None:
            self._record_attempt({**base, "stage": "suppressed", "reason": reason}, dedupe_abs=True)
            return False
        try:
            if isinstance(self._speaker, WindowsSpeech):
                spoken = self._speaker.speak(message, event_id=event_id)
            else:
                spoken = self._speaker.speak(message)
        except Exception as exc:
            if hasattr(self._speaker, "error"):
                self._speaker.error = str(exc)
            self._record_attempt({**base, "stage": "error", "reason": "backend_error"})
            self._refresh_status()
            return False
        if not spoken:
            reason = "backend_error" if self._error or not getattr(self._speaker, "available", True) else "busy"
            self._record_attempt({**base, "stage": "suppressed", "reason": reason}, dedupe_abs=True)
            self._refresh_status()
            return False
        self._record_attempt({**base, "stage": "accepted", "reason": None})
        if isinstance(self._speaker, WindowsSpeech):
            self._native_metadata[event_id] = base
        self.last_message = message
        self._last_message_kind = kind
        self._last_global_speech = now
        self._last_type_speech[kind] = now
        self._last_heartbeat = now
        self._refresh_status()
        return True

    def _record_attempt(self, event: dict, dedupe_abs: bool = False) -> None:
        if dedupe_abs and event["cue"] == "abs":
            reason = event["reason"]
            if reason in self._abs_logged_suppression:
                return
            self._abs_logged_suppression.add(reason)
        self._enqueue_event(event)

    def set_enabled(self, enabled: bool) -> bool:
        if type(enabled) is not bool:
            raise TypeError("enabled debe ser bool")
        if enabled == self.enabled:
            return self.enabled
        self.enabled = enabled
        self._clear_history()
        if enabled:
            self.last_sample = None
            self._sample_context = {"session_id": None, "time_s": None, "lap": None,
                                    "lap_dist_pct": None}
            now = self._clock()
            self._last_heartbeat = now
            # Demo/replay may only speak this explicit local test phrase.
            return self._say("enable", "Prueba de voz local. Telemetría de conducción solo en vivo.")
        self._speaker.purge()
        self._silenced = True
        self._last_global_speech = None
        self._last_type_speech.clear()
        self._last_heartbeat = None
        self._refresh_status()
        return True

    def test(self) -> bool:
        return self._say("test", "Prueba de voz local. Telemetría de conducción solo en vivo.")

    @staticmethod
    def _live_sample(sample, fresh: bool) -> bool:
        return bool(
            fresh and sample is not None and sample.source == "iracing"
            and sample.on_track is True and sample.is_replay is False
        )

    def _raw_throttle_event(self, sample) -> bool:
        # ponytail: tune these conservative gates only after collecting labeled traces.
        if (sample.throttle_raw is None or sample.vert_accel_mps2 is None
                or not 5 <= sample.vert_accel_mps2 <= 15 or sample.speed_mps is None
                or sample.speed_mps <= 10 or sample.steering_wheel_rad is None
                or abs(sample.steering_wheel_rad) <= 0.35 or sample.brake_raw is None
                or sample.brake_raw >= 0.03 or sample.gear is None or sample.gear < 2):
            return False
        if not self._history:
            return False
        previous = self._history[-1]
        elapsed = sample.time_s - previous.time_s
        return bool(
            0 < elapsed <= 0.35
            and previous.throttle_raw is not None
            and previous.vert_accel_mps2 is not None
            and 5 <= previous.vert_accel_mps2 <= 15
            and previous.speed_mps is not None and previous.speed_mps > 10
            and previous.brake_raw is not None and previous.brake_raw < 0.03
            and previous.gear == sample.gear and previous.gear >= 2
            and sample.throttle_raw - previous.throttle_raw > 0.45
        )

    def _stable_zone(self, sample) -> bool:
        return bool(sample.speed_mps is not None and sample.speed_mps > 15
                    and sample.brake is not None and sample.brake <= 0.02
                    and sample.steering_wheel_rad is not None
                    and abs(sample.steering_wheel_rad) < 0.1)

    def _navigation_hint(self, sample, navigation) -> bool:
        if (not navigation or not navigation.get("voice_ready")
                or navigation.get("session_id") != sample.session_id
                or navigation.get("sample_time_s") != sample.time_s):
            return False
        token = navigation.get("lap_token")
        if token is None:
            return False
        if token != self._navigation_lap:
            self._navigation_lap = token
            self._navigation_attempted.clear()
        upcoming = navigation.get("next")
        if (not upcoming or upcoming.get("seconds_ahead") is None
                or upcoming.get("location_basis") != "official_map_estimate"
                or not upcoming.get("corner_name") or not upcoming.get("cue")):
            return False
        zone = upcoming["id"]
        if zone not in (1, 2, 3) or zone in self._navigation_attempted:
            return False
        if not 4 <= upcoming["seconds_ahead"] <= 6:
            return False
        # ponytail: hasta tres curvas por vuelta; ampliar sólo tras probar la carga de voz.
        # Un aviso ocupado/omitido caduca aquí: nunca se reproduce tarde al llegar a la frenada.
        self._navigation_attempted.add(zone)
        self._say(f"navigation_{zone}", upcoming["cue"])
        return True

    def update(self, sample, fresh: bool, navigation=None) -> None:
        self.last_sample = sample
        self._sample_context = {
            "session_id": getattr(sample, "session_id", None),
            "time_s": getattr(sample, "time_s", None),
            "lap": getattr(sample, "lap", None),
            "lap_dist_pct": getattr(sample, "lap_dist_pct", None),
        } if sample is not None else {"session_id": None, "time_s": None, "lap": None,
                                      "lap_dist_pct": None}
        if not self.enabled:
            return
        if self.mode != "live" or not self._live_sample(sample, fresh):
            self._clear_history()
            if self._had_live_stream and not self._silenced:
                self._speaker.purge()
                self._silenced = True
            self._had_live_stream = False
            self._refresh_status()
            return

        reset = (self._session is not None and sample.session_id != self._session)
        if self._last_sample_time is not None:
            gap = sample.time_s - self._last_sample_time
            reset = reset or gap <= 0 or gap > 0.5
        if reset:
            self._clear_history(purge=True)
            self._silenced = True

        old_lap = self._last_lap
        self._had_live_stream = True
        self._silenced = False
        self._session = sample.session_id
        self._last_sample_time = sample.time_s

        stable = self._stable_zone(sample)
        if stable:
            if self._stable_since is None:
                self._stable_since = sample.time_s
        else:
            self._stable_since = None
        stable_long = self._stable_since is not None and sample.time_s - self._stable_since >= 1

        abs_event = False
        if sample.abs_active is True and sample.brake_raw is not None and sample.brake_raw > 0.15 \
                and sample.speed_mps is not None and sample.speed_mps > 15:
            if self._abs_since is None:
                self._abs_since = sample.time_s
            abs_event = sample.time_s - self._abs_since >= 0.35
            if abs_event and not self._abs_announced:
                if self._say("abs", "ABS sostenido. Suaviza el freno."):
                    self._abs_announced = True
        else:
            self._abs_since = None
            self._abs_announced = False
            self._abs_logged_suppression.clear()

        gas_event = self._raw_throttle_event(sample)
        lap_changed = old_lap is not None and sample.lap is not None and sample.lap > old_lap
        if sample.lap is not None:
            self._last_lap = sample.lap

        navigation_attempted = False
        if gas_event and not abs_event:
            self._say("gas", "Gas brusco. Aplica progresivo.")
        elif not abs_event and sample.abs_active is not True and stable_long:
            navigation_attempted = self._navigation_hint(sample, navigation)
        if not abs_event and not gas_event and not navigation_attempted and lap_changed and stable_long:
            self._say("lap", f"Vuelta {sample.lap}. Telemetría activa.")

        now = self._clock()
        if (stable_long and not abs_event and not gas_event and not navigation_attempted
                and not (lap_changed and stable_long)
                and self._last_heartbeat is not None and now - self._last_heartbeat >= 60):
            self._say("heartbeat", "Telemetría activa.")

        self._history.append(sample)
        self._refresh_status()

    def snapshot(self) -> dict:
        self._refresh_status()
        return {
            "enabled": self.enabled,
            "available": bool(getattr(self._speaker, "available", False)),
            "speaking": self._speaking(),
            "status": self.status,
            "last_message": self.last_message,
            "limitations": list(_LIMITATIONS),
        }

    def close(self) -> None:
        self.enabled = False
        self._clear_history(purge=True)
        self._speaker.close()
        self._refresh_status()
