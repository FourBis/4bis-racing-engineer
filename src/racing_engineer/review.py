"""Conservative vertical acceleration review with manually supplied markers."""

from __future__ import annotations

from math import isfinite
from typing import Iterable

from .model import Sample

G_MPS2 = 9.80665
# ponytail: extremos a 10 Hz y marcas manuales; mapas verificados/canales de suspensión permitirán más precisión.
MARKER_NAMES = ("5", "10b", "11")
_CLUSTER_GAP_S = 0.35
_MIN_SPEED_MPS = 5.0
_MARKER_RADIUS = 0.01


def validate_marker(data: object) -> dict:
    """Validate and copy a manual marker using normalized lap progress (0..1)."""
    if not isinstance(data, dict) or set(data) != {"name", "session_id", "progress_pct"}:
        raise ValueError("Marcador requiere exactamente name, session_id y progress_pct")
    name, session_id, progress = data["name"], data["session_id"], data["progress_pct"]
    if name not in MARKER_NAMES:
        raise ValueError("name debe ser 5, 10b o 11")
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 240:
        raise ValueError("session_id debe ser texto de 1 a 240 caracteres")
    if type(progress) not in (int, float) or not isfinite(progress) or not 0 <= progress <= 1:
        raise ValueError("progress_pct debe ser un número finito entre 0 y 1")
    return {"name": name, "session_id": session_id, "progress_pct": float(progress)}


def _eligible(sample: Sample) -> bool:
    speed = sample.speed_mps
    return (sample.on_track is True and sample.is_replay is False
            and type(speed) in (int, float) and isfinite(speed) and speed >= _MIN_SPEED_MPS)


def _valid_vertical(sample: Sample) -> bool:
    value = sample.vert_accel_mps2
    return type(value) in (int, float) and isfinite(value)


def _is_extreme(sample: Sample) -> bool:
    g = sample.vert_accel_mps2 / G_MPS2
    return g >= 2.0 or g <= 0.3


def _corner(progress: float | None, markers: list[dict]) -> str | None:
    if progress is None:
        return None
    matches = []
    for marker in markers:
        distance = abs(progress - marker["progress_pct"])
        distance = min(distance, 1.0 - distance)
        if distance <= _MARKER_RADIUS + 1e-12:
            matches.append((distance, marker["name"]))
    return min(matches)[1] if matches else None


def review_vertical(samples: Iterable[Sample], markers: Iterable[dict]) -> dict:
    """Find clustered vertical acceleration extremes in the final observed session."""
    rows = list(samples)
    session_id = rows[-1].session_id if rows else None
    manual = [validate_marker(marker) for marker in markers]
    manual = [marker for marker in manual if marker["session_id"] == session_id]

    groups: list[list[Sample]] = []
    active: list[Sample] = []
    previous_sample: Sample | None = None

    def flush() -> None:
        nonlocal active
        if active:
            groups.append(active)
            active = []

    for sample in rows:
        if sample.session_id != session_id:
            flush()
            previous_sample = None
            continue
        # Missing/invalid channels and excluded driving states break a cluster.
        if not _eligible(sample) or not _valid_vertical(sample):
            flush()
            previous_sample = None
            continue
        if previous_sample is not None and (
            sample.lap != previous_sample.lap
            or sample.time_s <= previous_sample.time_s
            or sample.time_s - previous_sample.time_s > _CLUSTER_GAP_S
        ):
            flush()
        if _is_extreme(sample):
            if (active and (sample.lap != active[-1].lap
                            or sample.time_s - active[-1].time_s > _CLUSTER_GAP_S)):
                flush()
            active.append(sample)
        previous_sample = sample
    flush()

    events = []
    for group in groups:
        # Highest excursion from 1 g is the single representative event sample.
        peak = max(group, key=lambda row: abs(row.vert_accel_mps2 / G_MPS2 - 1.0))
        progress = peak.lap_dist_pct
        corner = _corner(progress, manual)
        events.append({
            "lap": peak.lap,
            "time_s": peak.time_s,
            "lap_dist_pct": progress,
            "vert_g": peak.vert_accel_mps2 / G_MPS2,
            "speed_kmh": peak.speed_mps * 3.6,
            "throttle_raw": peak.throttle_raw,
            "brake_raw": peak.brake_raw,
            "corner": corner,
        })

    by_corner = []
    for name in MARKER_NAMES:
        values = [event["vert_g"] for event in events if event["corner"] == name]
        by_corner.append({
            "name": name,
            "events": len(values),
            "laps": len({event["lap"] for event in events if event["corner"] == name}),
            # peak_g is the largest signed representative event value.
            "peak_g": max(values) if values else None,
            "min_g": min(values) if values else None,
        })

    available = any(row.session_id == session_id and _valid_vertical(row) for row in rows)
    note = (
        "VertAccel incluye la gravedad y no mide recorrido de suspensión ni carga/contacto individual de neumáticos. "
        "Los marcadores de curvas son manuales y se asignan con tolerancia circular de ±1 % de vuelta."
    )
    if not available:
        note += " VertAccel no está disponible en esta sesión."
    return {"session_id": session_id, "markers": manual, "events": events,
            "by_corner": by_corner, "available": available, "note": note}
