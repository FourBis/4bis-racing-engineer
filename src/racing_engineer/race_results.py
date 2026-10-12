"""Balance descriptivo de la última sesión observada; no diagnostica causas."""

from collections import Counter

from .analysis import _interpolate, _lap_groups, _time_gate


_IDENTITY = ("source", "car_id", "car_name", "track_id", "track_name", "track_config")
_CHANNELS = ("speed_mps", "brake", "throttle", "steering_wheel_rad")
_LIMITS = [
    "Ranking por tiempo derivado, no por calidad técnica ni tiempo oficial.",
    "Tramos de 5% del progreso; no identifican curvas. Diferencias son correlaciones, no causas.",
    "ABS mide intervención reportada; no demuestra bloqueo ni patinaje físico.",
    "Medias y duraciones de mandos usan intervalos observados; observed_s informa su cobertura.",
]


def _stopped(samples):
    # ponytail: 2 s bajo 2 m/s detectan detención; mejorar con estados de boxes etiquetados.
    start = None
    for sample in samples:
        if sample.speed_mps is not None and sample.speed_mps < 2:
            start = sample.time_s if start is None else start
            if sample.time_s - start >= 2:
                return True
        else:
            start = None
    return False


def _reason(lap, identity, latest_run):
    samples = lap.samples
    for name in _IDENTITY:
        values = {getattr(s, name) for s in samples if getattr(s, name) is not None}
        if len(values) > 1 or (values and identity[name] not in values):
            return "identidad de fuente, auto, pista o layout inconsistente"
    if lap.run_number != latest_run:
        return "reinicio del reloj: vuelta de un bloque anterior"
    if not lap.complete:
        return "vuelta incompleta: falta observar inicio o cruce final"
    if any(s.is_replay is not False for s in samples):
        return "replay activo o desconocido"
    if any(s.on_track is not True for s in samples):
        return "estado en pista desconocido o fuera de pista"
    ok, reason = _time_gate(samples)
    if not ok:
        return reason
    if any(s.lap_dist_pct is None for s in samples):
        return "progreso de vuelta desconocido"
    for a, b in zip(samples, samples[1:]):
        distance = b.lap_dist_pct - a.lap_dist_pct
        if distance < 0 or distance > 0.1:
            return "retroceso o salto de progreso inválido"
        if (a.track_length_m and a.speed_mps is not None and b.speed_mps is not None
                and distance * a.track_length_m > max(a.speed_mps, b.speed_mps) * (b.time_s - a.time_s) * 3 + 10):
            return "salto de progreso incompatible con tiempo y velocidad"
    if any(getattr(s, "on_pit_road", None) is True for s in samples):
        return "paso por boxes reportado"
    if _stopped(samples):
        return "detención sostenida observada; ubicación de boxes no determinada"
    return ""


def _context(lap):
    samples = lap.samples
    result = []
    labels = {"healthy": "sano", "damaged": "con daño", "unknown": "desconocido",
              "clear": "despejado", "traffic": "tráfico"}
    for channel, label in (("damage", "Daño"), ("traffic", "Tráfico")):
        result.append(f"{label}: {', '.join(sorted({labels[getattr(s, channel)] for s in samples}))}.")
    if any(getattr(s, "on_pit_road", None) is None for s in samples):
        result.append("Canal de boxes desconocido en parte o toda la vuelta.")
    if any(getattr(s, name) is None for s in samples for name in ("car_id", "track_id", "track_config")):
        result.append("Identidad de auto, pista o layout parcialmente desconocida.")
    incidents = lap.incident_counts
    delta = incidents[-1] - incidents[0] if all(n is not None for n in incidents) else None
    if delta is None:
        result.append("Contador de incidentes desconocido.")
    elif any(b < a for a, b in zip(incidents, incidents[1:])):
        result.append("Contador de incidentes retrocede; delta no disponible.")
        delta = None
    else:
        result.append(f"Incidentes acumulados al inicio: {incidents[0]}; aumento observado: {delta}. No acredita daño.")
    return result, delta


def _time(lap, progress):
    if progress == 0:
        return lap.start_time_s
    if progress == 1:
        return lap.end_time_s
    return _interpolate(lap, progress, "time_s")


def _stats(lap, lo, hi):
    start, end = _time(lap, lo), _time(lap, hi)
    entry, exit_ = (_interpolate(lap, progress, "speed_mps") for progress in (lo, hi))
    stats = {"duration_s": None if start is None or end is None else end - start,
             "speed_entry_kmh": None if entry is None else entry * 3.6,
             "speed_exit_kmh": None if exit_ is None else exit_ * 3.6,
             "speed_mean_kmh": None, "brake_mean_pct": None, "throttle_mean_pct": None,
             "abs_active_s": None, "coast_s": None, "brake_throttle_overlap_s": None,
             "observed_s": 0.0}
    if start is None or end is None:
        return stats
    totals = {name: 0.0 for name in ("speed_mps", "brake", "throttle", "abs_active", "coast", "overlap")}
    missing = set()
    for a, b in zip(lap.samples, lap.samples[1:]):
        left, right = max(a.time_s, start), min(b.time_s, end)
        if right <= left:
            continue
        dt = right - left
        stats["observed_s"] += dt
        for name in ("speed_mps", "brake", "throttle"):
            v0, v1 = getattr(a, name), getattr(b, name)
            if v0 is None or v1 is None:
                missing.add(name)
                continue
            slope = (v1 - v0) / (b.time_s - a.time_s)
            totals[name] += (v0 + slope * ((left + right) / 2 - a.time_s)) * dt
        if a.abs_active is None or b.abs_active is None:
            missing.add("abs_active")
        else:
            totals["abs_active"] += dt if a.abs_active else 0
        if any(v is None for v in (a.brake, a.throttle, b.brake, b.throttle)):
            missing.update(("coast", "overlap"))
        else:
            totals["coast"] += dt if a.brake <= 0.02 and a.throttle <= 0.02 else 0
            totals["overlap"] += dt if a.brake > 0.05 and a.throttle > 0.05 else 0
    observed = stats["observed_s"]
    if observed:
        for channel, key, scale in (("speed_mps", "speed_mean_kmh", 3.6),
                                    ("brake", "brake_mean_pct", 100),
                                    ("throttle", "throttle_mean_pct", 100)):
            if channel not in missing:
                stats[key] = totals[channel] / observed * scale
        for channel, key in (("abs_active", "abs_active_s"), ("coast", "coast_s"),
                             ("overlap", "brake_throttle_overlap_s")):
            if channel not in missing:
                stats[key] = totals[channel]
    return stats


def race_results(samples, review_lap_id=None):
    """Revisar mejor y más lenta completas de un único bloque de sesión/identidad."""
    result = {"session_id": None, "status": "insufficient", "laps": [], "best": None,
              "worst": None, "delta_s": None, "segments": [],
              "selection": "automatic" if review_lap_id is None else "manual",
              "comparison": {"status": "insufficient", "first_id": None,
                             "second_id": None, "points": [], "limitations": " ".join(_LIMITS)},
              "limitations": _LIMITS.copy()}
    if not samples:
        if review_lap_id is not None:
            raise ValueError("Vuelta solicitada no encontrada en la sesión actual.")
        return result
    session = samples[-1].session_id
    start = len(samples) - 1
    while start > 0 and samples[start - 1].session_id == session:
        start -= 1
    samples = samples[start:]
    result["session_id"] = session
    if any(s.on_pit_road is None for s in samples):
        result["limitations"].append("No se puede descartar paso por boxes en movimiento: el canal de boxes está parcial o totalmente ausente.")
    if any(s.speed_mps is None for s in samples):
        result["limitations"].append("Velocidad parcialmente ausente: no se pueden descartar detenciones en los intervalos sin lectura.")
    identity = {name: next((getattr(s, name) for s in reversed(samples)
                            if getattr(s, name) is not None), None) for name in _IDENTITY}
    groups = _lap_groups(samples)
    latest_run = max((lap.run_number for lap in groups
                      if lap.samples[0].source == identity["source"]), default=0)
    eligible = []
    for lap in groups:
        reason = _reason(lap, identity, latest_run)
        context, incident_delta = _context(lap)
        labels = Counter(s.lap for s in lap.samples if s.lap is not None).most_common(1)
        row = {"id": lap.id, "lap": labels[0][0] if labels else None,
               "duration_s": lap.end_time_s - lap.start_time_s,
               "review_eligible": not reason, "exclusion_reason": reason,
               "context": context, "metrics": {"incidents_delta": incident_delta}}
        result["laps"].append(row)
        if not reason:
            eligible.append((row, lap))
    selected = None
    if review_lap_id is not None:
        selected = next((pair for pair in eligible if pair[0]["id"] == review_lap_id), None)
        if selected is None:
            raise ValueError("Vuelta solicitada inexistente o no aprovechable en la sesión actual.")
    if len(eligible) < 2:
        result["limitations"].append("Se requieren al menos dos vueltas completas aprovechables de la misma sesión e identidad.")
        return result
    references = [pair for pair in eligible if pair[0]["metrics"]["incidents_delta"] == 0
                  and all(s.damage != "damaged" for s in pair[1].samples)]
    if not references:
        result["limitations"].append("No hay referencia con contador conocido, sin aumento de incidentes y sin daño reportado; las vueltas siguen disponibles para revisión.")
        return result
    best, first = min(references, key=lambda pair: pair[0]["duration_s"])
    if selected is not None and selected[0]["id"] == best["id"]:
        raise ValueError("Selecciona una vuelta distinta de la referencia.")
    candidates = [pair for pair in eligible if pair[0]["id"] != best["id"]
                  and pair[0]["duration_s"] >= best["duration_s"] - 1e-9]
    if selected is None and not candidates:
        result["best"] = best
        result["limitations"].append("No hay otra vuelta aprovechable igual o más lenta que la referencia; selecciona una vuelta para revisión manual.")
        return result
    worst, second = selected or max(candidates, key=lambda pair: pair[0]["duration_s"])
    delta = worst["duration_s"] - best["duration_s"]
    result.update(status="ok", best=best, worst=worst,
                  delta_s=0.0 if abs(delta) < 1e-9 else delta)
    comparison = result["comparison"]
    comparison.update(status="ok", first_id=first.id, second_id=second.id)
    comparison["limitations"] += " Revisión descriptiva con errores y contexto desconocido permitidos; no certifica vueltas limpias."
    for index in range(101):
        progress = index / 100
        t1, t2 = _time(first, progress), _time(second, progress)
        if t1 is not None and t2 is not None:
            comparison["points"].append({
                "progress_pct": index,
                "first": {name: _interpolate(first, progress, name) for name in _CHANNELS},
                "second": {name: _interpolate(second, progress, name) for name in _CHANNELS},
                "delta_s": (t2 - second.start_time_s) - (t1 - first.start_time_s),
            })
    # ponytail: conservar 20 tramos como detalle para pistas sin zonas conocidas.
    sections = []
    for index in range(20):
        lo, hi = index / 20, (index + 1) / 20
        a, b = _stats(first, lo, hi), _stats(second, lo, hi)
        if a["duration_s"] is None or b["duration_s"] is None:
            continue
        sections.append({"start_pct": lo * 100, "end_pct": hi * 100,
                         "delta_s": b["duration_s"] - a["duration_s"], "first": a, "second": b})
    result["segments"] = sorted((s for s in sections if s["delta_s"] > 0.000001),
                                key=lambda s: s["delta_s"], reverse=True)[:3]
    from .corner_review import corner_review

    result["corner_review"] = corner_review(first, second)
    return result
