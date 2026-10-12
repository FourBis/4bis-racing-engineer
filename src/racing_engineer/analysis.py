"""Conservative analysis of normalized, local telemetry samples."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Iterable

from .model import Sample

_MAX_GAP_S = 0.5
_BRAKE_STEP = 0.55
_PULSE_PEAK = 0.25
_HIGH_THROTTLE = 0.70
_BRAKE_RESIDUAL = 0.10
_BOUNDARY_LOW = 0.03
_BOUNDARY_HIGH = 0.95
# ponytail: fixed thresholds keep the MVP explainable; upgrade after labeled traces exist.


@dataclass
class _Lap:
    id: str
    samples: list[Sample] = field(default_factory=list)
    start_boundary: bool = False
    end_boundary: bool = False
    start_time_s: float | None = None
    end_time_s: float | None = None
    run_number: int = 0

    @property
    def complete(self) -> bool:
        return self.start_boundary and self.end_boundary


def _finite(value: float | int | None) -> bool:
    return value is not None and isfinite(value)


def _lap_groups(samples: Iterable[Sample]) -> list[_Lap]:
    """Split on observed progress wraps; the SDK lap counter may update late."""
    result: list[_Lap] = []
    active: dict[tuple[str, str], _Lap] = {}
    run: dict[tuple[str, str], int] = {}
    index: dict[tuple[str, str], int] = {}
    previous_time: dict[tuple[str, str], float] = {}
    previous_progress: dict[tuple[str, str], float | None] = {}

    def new_lap(key: tuple[str, str], sample: Sample, at_boundary: bool,
                start_time_s: float | None = None) -> _Lap:
        index[key] = index.get(key, 0) + 1
        lap = _Lap(f"{key[0]}:{key[1]}:run{run.get(key, 0)}:segment{index[key]}",
                   [sample], at_boundary, False,
                   sample.time_s if start_time_s is None else start_time_s,
                   sample.time_s, run.get(key, 0))
        active[key] = lap
        return lap

    for sample in samples:
        key = (sample.source, sample.session_id)
        progress = sample.lap_dist_pct
        previous = previous_progress.get(key)
        old_time = previous_time.get(key)
        if old_time is not None and sample.time_s <= old_time:
            old = active.get(key)
            if old:
                old.end_time_s = old.samples[-1].time_s
                result.append(old)
            run[key] = run.get(key, 0) + 1
            previous_progress[key] = None
            previous = None
            active.pop(key, None)

        lap = active.get(key)
        if lap and _finite(previous) and _finite(progress) and previous >= _BOUNDARY_HIGH and progress <= _BOUNDARY_LOW:
            dt = sample.time_s - lap.samples[-1].time_s
            denominator = 1 - previous + progress
            crossed_at = (lap.samples[-1].time_s + (1 - previous) / denominator * dt
                          if denominator > 0 else lap.samples[-1].time_s)
            observed = 0 < dt <= _MAX_GAP_S
            lap.end_time_s = crossed_at
            lap.end_boundary = observed
            result.append(lap)
            lap = new_lap(key, sample, at_boundary=observed, start_time_s=crossed_at)
        elif lap is None:
            lap = new_lap(key, sample, at_boundary=_finite(progress) and progress == 0.0)
        elif (_finite(previous) and _finite(progress) and progress < previous - 0.02):
            lap.end_time_s = lap.samples[-1].time_s
            result.append(lap)
            lap = new_lap(key, sample, at_boundary=False)
        else:
            lap.samples.append(sample)
            lap.end_time_s = sample.time_s

        previous_time[key] = sample.time_s
        previous_progress[key] = progress if _finite(progress) else None

    for lap in active.values():
        if lap not in result:
            lap.end_time_s = lap.samples[-1].time_s
            result.append(lap)
    return sorted(result, key=lambda lap: lap.samples[0].time_s)


def _progress_range(samples: list[Sample]) -> tuple[float | None, float | None]:
    values = [s.lap_dist_pct for s in samples if _finite(s.lap_dist_pct)]
    return (min(values), max(values)) if values else (None, None)


def _time_gate(samples: list[Sample]) -> tuple[bool, str]:
    if any(b.time_s <= a.time_s for a, b in zip(samples, samples[1:])):
        return False, "reinicio o tiempo no creciente"
    if any(b.time_s - a.time_s > _MAX_GAP_S for a, b in zip(samples, samples[1:])):
        return False, "brecha de telemetría mayor a 0,5 s"
    return True, ""


def _progress_gate(samples: list[Sample]) -> tuple[bool, str]:
    if any(not _finite(s.lap_dist_pct) for s in samples):
        return False, "progreso de vuelta desconocido"
    progress = [s.lap_dist_pct for s in samples]
    if any(b < a - 0.02 for a, b in zip(progress, progress[1:])):
        return False, "retroceso de progreso"
    return True, ""


def _observation_gate(samples: list[Sample]) -> tuple[bool, str]:
    """Input observations need track/time/replay evidence, not known damage or traffic."""
    if any(s.is_replay is not False for s in samples):
        return False, "replay desconocido o activo"
    if any(s.on_track is not True for s in samples):
        return False, "estado en pista desconocido o fuera de pista"
    return _time_gate(samples)


def _comparison_gate(lap: _Lap) -> tuple[bool, str]:
    if not lap.complete:
        if not lap.start_boundary:
            return False, "inicio de vuelta no observado en meta"
        return False, "cruce final no observado"
    ok, reason = _observation_gate(lap.samples)
    if not ok:
        return ok, reason
    ok, reason = _progress_gate(lap.samples)
    if not ok:
        return ok, reason
    if any(s.damage != "healthy" for s in lap.samples):
        return False, "daño desconocido o distinto de sano"
    if any(s.traffic != "clear" for s in lap.samples):
        return False, "tráfico desconocido o no despejado"
    incidents = [s.incidents for s in lap.samples]
    if any(value is None for value in incidents):
        return False, "contador de incidentes desconocido"
    if any(value != 0 for value in incidents):
        return False, "contacto/incidente reportado; estado posterior requiere revisión"
    return True, ""


def _finding(category: str, observation: str, interpretation: str, confidence: str,
             lap: _Lap, start: Sample, end: Sample, next_test: str, **details: object) -> dict:
    return {
        "category": category,
        "observation": observation,
        "interpretation": interpretation,
        "confidence": confidence,
        "evidence": {"session_id": start.session_id, "start_s": start.time_s,
                     "end_s": end.time_s, "lap_id": lap.id, **details},
        "limitations": (
            "Los mandos son señales del simulador; no prueban presión física ni falla de hardware. "
            f"Daño: {','.join(sorted({s.damage for s in lap.samples}))}; "
            f"tráfico: {','.join(sorted({s.traffic for s in lap.samples}))}."
        ),
        "next_test": next_test,
    }


def _straight_candidates(lap: _Lap) -> list[dict]:
    candidates: list[dict] = []
    run: list[Sample] = []

    def save() -> None:
        if run and run[-1].time_s - run[0].time_s >= 1.0:
            candidates.append({
                "start_pct": run[0].lap_dist_pct * 100,
                "end_pct": run[-1].lap_dist_pct * 100,
                "start_s": run[0].time_s,
                "end_s": run[-1].time_s,
            })

    for sample in lap.samples:
        complete_inputs = all(_finite(value) for value in (
            sample.lap_dist_pct, sample.throttle, sample.brake, sample.steering_wheel_rad,
        ))
        is_candidate = (complete_inputs and sample.throttle >= 0.95 and sample.brake <= 0.02
                        and abs(sample.steering_wheel_rad) <= 0.1)
        if (not is_candidate or (run and sample.time_s - run[-1].time_s > _MAX_GAP_S)):
            save()
            run = []
        if is_candidate:
            run.append(sample)
    save()
    return candidates


def _lap_findings(lap: _Lap) -> list[dict]:
    findings: list[dict] = []
    samples = lap.samples
    # ponytail: fixed 3-point cues are review prompts, not pedal or car diagnoses.
    for i in range(len(samples) - 1):
        a, b = samples[i:i + 2]
        dt = b.time_s - a.time_s
        if (_finite(a.brake) and _finite(b.brake) and 0 < dt <= 0.15
                and b.brake - a.brake >= _BRAKE_STEP and i + 2 < len(samples)):
            c = samples[i + 2]
            if (_finite(c.brake) and c.time_s - b.time_s <= 0.15
                    and c.brake >= b.brake * 0.7):
                findings.append(_finding(
                    "brake_application", "El comando de freno sube abruptamente y se mantiene.",
                    "Aplicación rápida o respuesta brusca; no demuestra falla del sensor.", "media", lap, a, b,
                    "Repetir el mismo punto y revisar Brake junto con BrakeRaw.",
                    brake_before=a.brake, brake_after=b.brake,
                ))
                break

    for a, b, c in zip(samples, samples[1:], samples[2:]):
        if (all(_finite(s.brake) for s in (a, b, c)) and a.brake >= 0.4
                and b.brake <= a.brake - 0.15 and c.brake >= b.brake + 0.15
                and c.time_s - a.time_s <= 0.35):
            findings.append(_finding(
                "brake_release", "El comando de freno cae y vuelve a subir durante la liberación.",
                "La liberación no es monotónica en este tramo; puede ser una corrección deliberada.",
                "baja", lap, a, c, "Revisar el mismo punto en varias vueltas y contrastar Brake con BrakeRaw.",
                brake_values=[a.brake, b.brake, c.brake],
            ))
            break

    pulses = []
    for a, peak, b in zip(samples, samples[1:], samples[2:]):
        if (all(_finite(s.brake) for s in (a, peak, b)) and peak.brake >= _PULSE_PEAK
                and a.brake <= 0.05 and b.brake <= 0.05
                and peak.time_s - a.time_s <= 0.15 and b.time_s - peak.time_s <= 0.15):
            pulses.append(peak)
    if len(pulses) >= 2:
        findings.append(_finding(
            "pedal_signal_candidate", f"Se observan {len(pulses)} pulsos aislados de freno.",
            "Es candidato de señal; accionamientos deliberados breves pueden verse igual.", "baja", lap,
            pulses[0], pulses[-1], "Mantener el pedal quieto y registrar Brake y BrakeRaw.",
            peaks=[p.brake for p in pulses],
        ))

    overlap: list[Sample] = []
    def save_overlap() -> None:
        if len(overlap) >= 2 and overlap[-1].time_s - overlap[0].time_s >= 0.2:
            findings.append(_finding(
                "brake_residual", "Coinciden freno ≥0,10 y acelerador ≥0,70 por al menos 0,2 s.",
                "Hay solapamiento de mandos; puede ser técnica o una señal que no retorna a cero.", "media", lap,
                overlap[0], overlap[-1], "Revisar el retorno de freno y BrakeRaw en ese tramo.",
            ))

    for sample in samples:
        active = (_finite(sample.brake) and _finite(sample.throttle)
                  and sample.brake >= _BRAKE_RESIDUAL and sample.throttle >= _HIGH_THROTTLE)
        if not active or (overlap and sample.time_s - overlap[-1].time_s > 0.15):
            save_overlap()
            overlap = []
        if active:
            overlap.append(sample)
    save_overlap()

    for section in _straight_candidates(lap):
        start = next(s for s in samples if s.time_s == section["start_s"])
        end = next(s for s in reversed(samples) if s.time_s == section["end_s"])
        findings.append(_finding(
            "straight_candidate", "Hay un tramo con gas ≥0,95, freno ≤0,02 y volante ±0,1 rad durante ≥1 s.",
            "Candidato de baja dirección; la forma del circuito y rebufo no están determinados.", "baja", lap,
            start, end, "Comparar el mismo intervalo por distancia con tráfico/rebufo observado.",
            start_pct=section["start_pct"], end_pct=section["end_pct"],
        ))
    return findings


def summarize(samples: list[Sample]) -> dict:
    laps = _lap_groups(samples)
    rows, findings = [], []
    for lap in laps:
        eligible, reason = _comparison_gate(lap)
        observations_ok, _ = _observation_gate(lap.samples)
        duration = lap.end_time_s - lap.start_time_s if lap.end_time_s is not None else None
        rows.append({
            "id": lap.id, "label": lap.id.split(":")[-1],
            "source": lap.samples[0].source, "session_id": lap.samples[0].session_id,
            "lap": lap.samples[0].lap, "duration_s": duration,
            "complete": lap.complete, "eligible": eligible,
            "reason": reason or "",
        })
        if observations_ok:
            findings.extend(_lap_findings(lap))
    has_gap = any(not _time_gate(lap.samples)[0] for lap in laps)
    replay = any(s.is_replay is True for s in samples)
    sources = {s.source for s in samples}
    mode_parts = []
    if "demo" in sources:
        mode_parts.append("SINTÉTICA")
    if replay:
        mode_parts.append("REPLAY")
    if not mode_parts:
        mode_parts.append("LIVE")
    return {
        "laps": rows, "findings": findings, "comparison": None,
        "sample_count": len(samples),
        "mode": "/".join(mode_parts),
        "truncated": any(not lap.complete for lap in laps) or has_gap,
    }


def _interpolate(lap: _Lap, progress: float, channel: str) -> float | int | None:
    samples = lap.samples
    for sample in samples:
        if _finite(sample.lap_dist_pct) and sample.lap_dist_pct == progress:
            return getattr(sample, channel)
    for a, b in zip(samples, samples[1:]):
        p0, p1 = a.lap_dist_pct, b.lap_dist_pct
        if not (_finite(p0) and _finite(p1) and p0 <= progress <= p1 and p1 > p0):
            continue
        dt = b.time_s - a.time_s
        v0, v1 = getattr(a, channel), getattr(b, channel)
        if dt <= 0 or dt > _MAX_GAP_S or v0 is None or v1 is None:
            return None
        if channel == "gear":
            return v0
        return v0 + (progress - p0) / (p1 - p0) * (v1 - v0)
    return None


def _subtract(second: float | int | None, first: float | int | None) -> float | int | None:
    return second - first if second is not None and first is not None else None


def compare_laps(samples: list[Sample], first_id: str, second_id: str) -> dict:
    groups = {lap.id: lap for lap in _lap_groups(samples)}
    first, second = groups.get(first_id), groups.get(second_id)
    base = {"first_id": first_id, "second_id": second_id, "points": []}
    if first is None or second is None:
        return {**base, "status": "not_found", "reason": "vuelta no encontrada"}
    if first_id == second_id:
        return {**base, "status": "same_lap", "reason": "selecciona dos vueltas distintas"}
    if first.samples[0].source != second.samples[0].source or first.samples[0].session_id != second.samples[0].session_id:
        return {**base, "status": "different_session", "reason": "fuente o sesión distinta"}
    if first.run_number != second.run_number:
        return {**base, "status": "different_run", "reason": "reinicio del reloj entre vueltas"}
    for lap in (first, second):
        ok, reason = _comparison_gate(lap)
        if not ok:
            return {**base, "status": "ineligible", "reason": reason}

    bounds = [_progress_range(lap.samples) for lap in (first, second)]
    start = max(bounds[0][0], bounds[1][0])
    end = min(bounds[0][1], bounds[1][1])
    start_times = [_interpolate(lap, start, "time_s") for lap in (first, second)]
    if any(value is None for value in start_times):
        return {**base, "status": "no_common_coverage", "reason": "sin origen temporal común"}

    points = []
    channels = ("brake", "throttle", "steering_wheel_rad", "speed_mps", "rpm", "gear")
    for index in range(21):
        progress = start + (end - start) * index / 20
        t1, t2 = _interpolate(first, progress, "time_s"), _interpolate(second, progress, "time_s")
        if t1 is None or t2 is None:
            continue
        a = {name: _interpolate(first, progress, name) for name in channels}
        b = {name: _interpolate(second, progress, name) for name in channels}
        points.append({
            "progress_pct": progress * 100,
            "first": a,
            "second": b,
            "delta_s": (t2 - start_times[1]) - (t1 - start_times[0]),
            "delta_brake": _subtract(b["brake"], a["brake"]),
            "delta_throttle": _subtract(b["throttle"], a["throttle"]),
            "delta_speed_mps": _subtract(b["speed_mps"], a["speed_mps"]),
        })
    if not points:
        return {**base, "status": "no_common_coverage", "reason": "sin cobertura común"}

    sections = []
    for c1 in _straight_candidates(first):
        for c2 in _straight_candidates(second):
            lo, hi = max(c1["start_pct"], c2["start_pct"]) / 100, min(c1["end_pct"], c2["end_pct"]) / 100
            if lo >= hi:
                continue
            entry = {"progress_pct": lo * 100}
            exit_ = {"progress_pct": hi * 100}
            for name in channels:
                entry[f"first_{name}"] = _interpolate(first, lo, name)
                entry[f"second_{name}"] = _interpolate(second, lo, name)
                exit_[f"first_{name}"] = _interpolate(first, hi, name)
                exit_[f"second_{name}"] = _interpolate(second, hi, name)
            sections.append({
                "label": "tramo candidato; no confirma recta", "entry": entry, "exit": exit_,
                "speed_change_mps": {
                    "first": _subtract(exit_["first_speed_mps"], entry["first_speed_mps"]),
                    "second": _subtract(exit_["second_speed_mps"], entry["second_speed_mps"]),
                },
            })
    return {
        **base, "status": "ok", "points": points, "straight_sections": sections,
        "limitations": "Mismo tramo por progreso; rebufo no está medido y puede explicar diferencias de velocidad.",
    }


def report_markdown(summary: dict) -> str:
    laps = summary.get("laps", [])
    source_mode = str(summary.get("source_mode", summary.get("mode", "DESCONOCIDO"))).upper()
    mode = "REPLAY" if source_mode == "REPLAY" else source_mode
    if summary.get("synthetic") and "SINTÉTICO" not in mode and "SINTÉTICA" not in mode:
        mode += " SINTÉTICO"
    analyzed = summary.get("samples_analyzed", summary.get("sample_count", 0))
    received = summary.get("samples_received", analyzed)
    partial_count = sum(not lap.get("complete", False) for lap in laps)
    lines = ["# Informe de telemetría", "",
             f"Modo: {mode} · Muestras analizadas/recibidas: {analyzed}/{received}",
             f"Ventana de memoria truncada: {'sí' if summary.get('memory_window_truncated') else 'no'}",
             f"Vueltas parciales: {partial_count} · Vueltas totales: {len(laps)}", ""]
    annotations = summary.get("operator_annotations") or []
    if annotations:
        lines.append(f"Anotaciones del operador: {annotations}")
        lines.append("")
    balance = summary.get("race_results")
    if balance:
        lines.extend(["## Balance de vueltas", "Comparación descriptiva; tiempos derivados, no oficiales."])
        if balance.get("status") == "ok":
            best, other = balance["best"], balance["worst"]
            lines.extend([
                f"Referencia: vuelta {best['lap']}, {best['duration_s']:.3f} s.",
                f"Revisión: vuelta {other['lap']}, {other['duration_s']:.3f} s; diferencia {balance['delta_s']:+.3f} s.",
            ])
            for segment in balance.get("segments", []):
                lines.append(f"- Tramo {segment['start_pct']:.0f}–{segment['end_pct']:.0f}%: "
                             f"{segment['delta_s']:+.3f} s. Revisar trazas de velocidad y pedales en el panel.")
        for row in balance.get("laps", []):
            state = "analizable" if row["review_eligible"] else f"excluida: {row['exclusion_reason']}"
            lines.append(f"- Vuelta {row['lap']}: {state}. {' '.join(row['context'])}")
        lines.extend([*balance.get("limitations", []), "", "## Comparación estricta", ""])
    for lap in laps:
        duration = lap.get("duration_s")
        derived = f"duración derivada {duration:.2f} s (no oficial)" if duration is not None else "duración no disponible"
        state = "completa" if lap.get("complete") else "parcial"
        if not lap.get("eligible"):
            reason = lap.get("reason") or "criterios de comparación no cumplidos"
            lines.append(f"- {lap['id']}: {state}, {derived}; no elegible — {reason}.")
        else:
            lines.append(f"- {lap['id']}: {state}, {derived}; elegible para comparación.")
    findings = summary.get("findings", [])
    if findings:
        lines.append("")
        for item in findings:
            evidence = item["evidence"]
            lines.extend([
                f"## {item['category']}", f"- Observación: {item['observation']}",
                f"- Interpretación: {item['interpretation']}", f"- Confianza: {item['confidence']}",
                f"- Evidencia: sesión {evidence['session_id']}, {evidence['start_s']:.2f}–{evidence['end_s']:.2f} s.",
                f"- Límite: {item['limitations']}", "",
            ])
    else:
        lines.extend(["El detector de patrones no agregó otros hallazgos." if balance and balance.get("status") == "ok"
                      else "No hay hallazgos respaldados con los datos disponibles.", ""])
    comparison = summary.get("comparison")
    if comparison:
        lines.extend(["## Comparación seleccionada", f"Estado: {comparison['status']}",
                      f"A: {comparison['first_id']}", f"B: {comparison['second_id']}"])
        points = comparison.get("points", [])
        if points:
            lines.append(f"Cobertura común: {points[0]['progress_pct']:.2f}–{points[-1]['progress_pct']:.2f} %. "
                         f"Delta B−A al final de esa cobertura: {points[-1]['delta_s']:+.3f} s.")
        lines.extend([comparison.get("limitations") or comparison.get("reason", ""), ""])
    next_test = findings[0]["next_test"] if findings else "Registrar una vuelta con límites de meta observados y estados de auto, tráfico y replay conocidos."
    if balance and balance.get("segments"):
        segment = balance["segments"][0]
        next_test = (f"Revisar primero el tramo {segment['start_pct']:.0f}–{segment['end_pct']:.0f}% "
                     f"({segment['delta_s']:+.3f} s): contrastar velocidad de entrada/salida y trazas de pedales "
                     "con la referencia, teniendo en cuenta tráfico e incidentes antes de atribuirlo a técnica.")
    lines.extend(["## Próxima prueba", next_test])
    review = summary.get("review")
    if review:
        lines.extend(["", "## Revisión vertical", review["note"],
                      f"Eventos candidatos: {len(review['events'])}. Marcas manuales: {len(review['markers'])}."])
    history = summary.get("voice_history")
    if history:
        lines.extend(["", "## Historial de voz", history["note"],
                      f"Eventos conservados: {len(history['events'])}; total observado: {history['total_events']}.",
                      "El JSON contiene etapas y timestamps. Son eventos, no un conteo de frases escuchadas."])
        if history.get("error"):
            lines.append(history["error"])
    navigation = summary.get("navigation")
    if navigation:
        track = navigation["track"]
        lines.extend(["", "## Guía de pista", navigation["status"],
                      f"Pista: {track.get('name') or 'sin identificar'}; "
                      f"auto: {track.get('car') or 'sin identificar'}."])
        reference = navigation.get("reference")
        if reference:
            lines.append(f"Referencia personal observada: vuelta {reference['lap']}, {reference['time_s']:.3f} s.")
        for zone in navigation.get("zones", []):
            name = zone.get("corner_name") or "Punto sin curva identificada"
            cue = zone.get("cue") or "Plan pendiente."
            lines.append(f"- {name}: {cue} Inicio observado: {zone['lap_dist_pct'] * 100:.2f}% de vuelta.")
        lines.append(navigation.get("map_note", "Ubicaciones pendientes de validar."))
        lines.append("La vuelta observada sirve para comparar; no acredita una frenada óptima. El JSON incluye deltas y comparación por tramo.")
    return "\n".join(lines)
