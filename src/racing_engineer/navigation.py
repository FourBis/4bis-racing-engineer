"""Small, session-local navigator based on the driver's observed laps."""

from __future__ import annotations

from dataclasses import dataclass

from .analysis import _Lap, _interpolate, _lap_groups
from .model import Sample

_MAX_GAP_S = 0.5
_MIN_SPEED_MPS = 5.0
_BRAKE_LOW = 0.05
_BRAKE_ON = 0.15
_ZONE_SEPARATION_S = 2.0
_NEXT_HORIZON_S = 30.0
# ponytail: three strongest zones and four fixed quarters; upgrade after map-verified corner identities exist.


def _brake_class(strength: float) -> str:
    return "fuerte" if strength >= 0.65 else "media" if strength >= 0.35 else "suave"


@dataclass
class _Zone:
    progress: float
    time_s: float
    speed_mps: float
    strength: float


class Navigator:
    """Keep a bounded live buffer and learn only from complete valid laps."""

    def __init__(self) -> None:
        self._lap_samples: list[Sample] = []
        self._reference: _Lap | None = None
        self._reference_time: float | None = None
        self._reference_lap_number: int | None = None
        self._zone_events: list[_Zone] = []
        self._comparison: dict | None = None
        self._identity: tuple | None = None
        self._run = 0
        self._lap_start_time: float | None = None
        self._lap_valid = True
        self._lap_truncated = False
        self._lap_reason = ""
        self._sample: Sample | None = None

    def update(self, sample: Sample) -> None:
        identity = (sample.source, sample.session_id, getattr(sample, "track_id", None),
                    getattr(sample, "track_config", None), getattr(sample, "car_id", None),
                    getattr(sample, "track_length_m", None))
        changed = self._identity is not None and identity != self._identity
        reversed_time = self._sample is not None and sample.time_s <= self._sample.time_s
        if changed or reversed_time:
            self._reset_run()
        self._identity = identity
        self._sample = sample

        previous = self._lap_samples[-1] if self._lap_samples else None
        if previous is None:
            self._lap_samples = [sample]
            self._lap_start_time = sample.time_s if sample.lap_dist_pct == 0 else None
            self._apply_sample_gates(sample, None)
            return

        progress, old_progress = sample.lap_dist_pct, previous.lap_dist_pct
        dt = sample.time_s - previous.time_s
        crossed = (old_progress is not None and progress is not None and
                   old_progress >= 0.95 and progress <= 0.03 and 0 < dt <= _MAX_GAP_S)
        if crossed:
            self._apply_sample_gates(sample, previous, wrapped=True)
            groups = _lap_groups(self._lap_samples + [sample])
            completed = next((lap for lap in reversed(groups) if lap.end_boundary), None)
            if completed is not None and not self._lap_truncated and self._lap_start_time is not None:
                completed.start_boundary = True
                completed.start_time_s = self._lap_start_time
                self._complete_lap(completed, completed.samples[-1].lap)
            self._lap_samples = [sample]
            self._lap_start_time = previous.time_s + dt * (1 - old_progress) / (1 - old_progress + progress)
            self._lap_valid = True
            self._lap_truncated = False
            self._lap_reason = ""
            self._apply_sample_gates(sample, None)
        else:
            self._lap_samples.append(sample)
            if len(self._lap_samples) > 12000:
                self._lap_truncated = True
                self._invalidate("vuelta excede el límite de 12000 muestras")
                del self._lap_samples[:6000]
            if self._lap_start_time is None and progress == 0.0:
                self._lap_start_time = sample.time_s

        if not crossed:
            self._apply_sample_gates(sample, previous)

    def snapshot(self) -> dict:
        sample = self._sample
        track = {
            "name": getattr(sample, "track_name", None) if sample else None,
            "layout": getattr(sample, "track_config", None) if sample else None,
            "length_m": getattr(sample, "track_length_m", None) if sample else None,
            "car": getattr(sample, "car_name", None) if sample else None,
            "track_id": getattr(sample, "track_id", None) if sample else None,
            "car_id": getattr(sample, "car_id", None) if sample else None,
        }
        ready = bool(sample and self._reference and track["track_id"] is not None and
                     track["layout"] is not None and track["car_id"] is not None and
                     track["length_m"] and sample.source == "iracing" and self._live_sample_valid())
        reason = ("Referencia y estado en pista disponibles." if ready else
                  "Audio desactivado: demo sintética." if sample and sample.source == "demo" else
                  "Audio desactivado: requiere referencia, pista activa, longitud e identidad completas.")
        if sample and sample.source == "demo":
            status = ("DEMO sintética; referencia personal observada, no óptima ni libre de tráfico."
                      if self._reference else "DEMO sintética; aún sin referencia personal completa.")
        elif self._reference is None:
            status = "Sin vuelta personal válida completa en esta sesión."
            if self._lap_reason:
                status += f" Última muestra: {self._lap_reason}."
        else:
            status = "Referencia personal observada; no demuestra vuelta óptima ni ausencia de tráfico."

        delta = self._current_delta() if self._live_sample_valid() else None
        next_zone = self._next_zone(track) if self._live_sample_valid() and self._reference else None
        return {
            "track": track,
            "status": status,
            "reference": (None if self._reference is None else {
                "lap": self._reference_lap_number, "time_s": self._reference_time,
                "kind": "personal_observed",
                "basis": "fastest_observed_lap", "target_validated": False,
            }),
            "zones": [{"id": index, "lap_dist_pct": zone.progress,
                       "reference_speed_kph": round(zone.speed_mps * 3.6, 1),
                       "reference_brake_peak": zone.strength,
                       "initial_brake_class": _brake_class(zone.strength)}
                      for index, zone in enumerate(self._zone_events, 1)],
            "next": next_zone,
            "delta_s": delta,
            "comparison": self._comparison,
            "voice_ready": ready,
            "voice_reason": reason,
            "session_id": sample.session_id if sample else None,
            "sample_time_s": sample.time_s if sample else None,
            "lap_token": self._lap_token(),
        }

    def _reset_run(self) -> None:
        self._lap_samples.clear()
        self._reference = None
        self._reference_time = None
        self._reference_lap_number = None
        self._zone_events = []
        self._comparison = None
        self._lap_start_time = None
        self._lap_valid = True
        self._lap_truncated = False
        self._lap_reason = ""
        self._run += 1

    def _apply_sample_gates(self, sample: Sample, previous: Sample | None,
                            *, wrapped: bool = False) -> None:
        if sample.is_replay is not False:
            self._invalidate("replay activo o desconocido")
        elif sample.on_track is not True:
            self._invalidate("fuera de pista o estado desconocido")
        elif sample.lap_dist_pct is None:
            self._invalidate("progreso desconocido")
        elif sample.speed_mps is None or sample.speed_mps < _MIN_SPEED_MPS:
            self._invalidate("detención o velocidad desconocida")
        elif sample.damage == "damaged":
            self._invalidate("daño explícito durante la vuelta")
        elif sample.incidents is None:
            self._invalidate("contador de incidentes desconocido")
        elif previous and sample.incidents != previous.incidents:
            self._invalidate("cambio en el contador de incidentes")
        if previous:
            dt = sample.time_s - previous.time_s
            if dt <= 0:
                self._invalidate("tiempo no creciente")
            elif dt > _MAX_GAP_S:
                self._invalidate("brecha de telemetría mayor a 0,5 s")
            p0, p1 = previous.lap_dist_pct, sample.lap_dist_pct
            if not wrapped and p0 is not None and p1 is not None and p1 < p0 - 0.02:
                self._invalidate("retroceso de progreso")
            elif not wrapped and p0 is not None and p1 is not None and p1 - p0 > 0.05:
                self._invalidate("salto de progreso compatible con teletransporte")

    def _invalidate(self, reason: str) -> None:
        self._lap_valid = False
        self._lap_reason = reason

    def _complete_lap(self, lap: _Lap, observed_lap_number: int | None) -> None:
        valid = self._lap_valid and self._lap_start_time is not None
        if not valid:
            return
        elapsed = lap.end_time_s - lap.start_time_s if lap.end_time_s is not None and lap.start_time_s is not None else None
        if elapsed is None or elapsed <= 0:
            return
        old_reference = self._reference
        if old_reference is not None:
            self._comparison = self._compare(lap, old_reference, observed_lap_number)
        if self._reference_time is None or elapsed < self._reference_time:
            self._reference = lap
            self._reference_time = elapsed
            self._reference_lap_number = observed_lap_number
            self._zone_events = self._braking_zones(lap)

    def _braking_zones(self, lap: _Lap) -> list[_Zone]:
        events: list[_Zone] = []
        first_brake = lap.samples[0].brake
        armed = first_brake is not None and first_brake <= _BRAKE_LOW
        index = 1
        while index < len(lap.samples):
            sample = lap.samples[index]
            brake = sample.brake
            if brake is None:
                armed = False
                index += 1
                continue
            if brake <= _BRAKE_LOW:
                armed = True
                index += 1
                continue
            if armed and brake >= _BRAKE_ON:
                peak = brake
                end = index + 1
                released = False
                while end < len(lap.samples):
                    value = lap.samples[end].brake
                    if value is None:
                        break
                    if value <= _BRAKE_LOW:
                        released = True
                        break
                    peak = max(peak, value)
                    end += 1
                if released and sample.lap_dist_pct is not None and sample.speed_mps is not None:
                    events.append(_Zone(sample.lap_dist_pct, sample.time_s, sample.speed_mps, peak))
                armed = False
                index = end
            else:
                index += 1
        selected: list[_Zone] = []
        for event in sorted(events, key=lambda item: item.strength, reverse=True):
            if all(abs(event.time_s - other.time_s) >= _ZONE_SEPARATION_S for other in selected):
                selected.append(event)
                if len(selected) == 3:
                    break
        selected.sort(key=lambda item: item.progress)
        return selected

    def _current_delta(self) -> float | None:
        if not self._reference or not self._lap_samples or self._sample is None:
            return None
        progress = self._sample.lap_dist_pct
        if progress is None or self._lap_start_time is None:
            return None
        reference_elapsed = self._time_at(self._reference, progress)
        current_elapsed = self._sample.time_s
        if reference_elapsed is None or current_elapsed is None:
            return None
        return round((current_elapsed - self._lap_start_time) -
                     (reference_elapsed - self._reference.start_time_s), 3)

    def _compare(self, lap: _Lap, reference: _Lap, lap_number: int | None) -> dict | None:
        points = [index / 4 for index in range(5)]
        times = [self._time_at(lap, point) for point in points]
        ref_times = [self._time_at(reference, point) for point in points]
        if any(value is None for value in times + ref_times):
            return None
        losses = [round((times[i + 1] - times[i]) - (ref_times[i + 1] - ref_times[i]), 3)
                  for i in range(4)]
        worst = max(range(4), key=lambda index: losses[index])
        return {"lap": lap_number, "time_s": round(lap.end_time_s - lap.start_time_s, 3),
                "delta_s": round((lap.end_time_s - lap.start_time_s) - self._reference_time, 3),
                "worst_segment": {"start_pct": points[worst], "end_pct": points[worst + 1],
                                  "loss_s": losses[worst]}}

    def _time_at(self, lap: _Lap, progress: float) -> float | None:
        first, last = lap.samples[0], lap.samples[-1]
        first_p, last_p = first.lap_dist_pct, last.lap_dist_pct
        if first_p is None or last_p is None or lap.start_time_s is None or lap.end_time_s is None:
            return None
        if progress <= first_p:
            return lap.start_time_s + (progress / first_p) * (first.time_s - lap.start_time_s) if first_p else lap.start_time_s
        if progress >= last_p:
            return last.time_s + (progress - last_p) / (1 - last_p) * (lap.end_time_s - last.time_s) if last_p < 1 else lap.end_time_s
        return _interpolate(lap, progress, "time_s")

    def _live_sample_valid(self) -> bool:
        sample = self._sample
        return bool(sample and self._lap_valid and sample.on_track is True and
                    sample.is_replay is False and sample.speed_mps is not None and
                    sample.speed_mps >= _MIN_SPEED_MPS and sample.lap_dist_pct is not None)

    def _next_zone(self, track: dict) -> dict | None:
        sample = self._sample
        if sample is None or sample.lap_dist_pct is None:
            return None
        options = []
        for index, zone in enumerate(self._zone_events, 1):
            pct = zone.progress
            forward = (pct - sample.lap_dist_pct) % 1.0
            if forward < 0.002:
                continue
            length = track["length_m"]
            speed = sample.speed_mps
            distance = forward * length if length is not None else None
            seconds = distance / speed if distance is not None and speed else None
            if seconds is not None and seconds > _NEXT_HORIZON_S:
                continue
            # ponytail: 30 s horizon avoids announcing next-lap zones early; upgrade with map-aware approach windows.
            options.append((forward, {"id": index, "lap_dist_pct": pct,
                                      "distance_m": round(distance, 1) if distance is not None else None,
                                      "seconds_ahead": round(seconds, 2) if seconds is not None else None,
                                      "reference_speed_kph": round(zone.speed_mps * 3.6, 1),
                                      "reference_brake_peak": zone.strength,
                                      "initial_brake_class": _brake_class(zone.strength)}))
        return min(options, key=lambda item: item[0])[1] if options else None

    def _lap_token(self) -> str | None:
        if self._lap_start_time is None:
            return None
        return f"{self._run}:{self._lap_start_time:.3f}"
