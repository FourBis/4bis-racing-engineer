"""Zonas aproximadas de Road Atlanta y reproducción temporal de dos vueltas."""

from collections import Counter

from .analysis import _interpolate


# ponytail: regiones amplias desde rótulos existentes; refinar con GPS/video validado.
_ZONES = (
    ("sf-t1", "Meta a preparación de curva 1", 0, 8),
    ("t1", "Curva 1", 8, 16),
    ("t2-t4", "Curvas 2–4", 16, 30),
    ("t5", "Curva 5", 30, 43),
    ("t6-t7", "Curvas 6–7", 43, 56),
    ("straight-t8-t9", "Recta y curvas 8–9", 56, 80),
    ("t10a-t10b", "Curvas 10a–10b", 80, 89),
    ("t11-t12", "Curvas 11–12 y salida a meta", 89, 100),
)
_NOTE = (
    "Mapa y zonas aproximados desde rótulos del circuito, sin GPS ni trazada medida. "
    "Los límites no son entradas, ápices ni salidas de curva comprobados. "
    "Replay local por tiempo desde meta de cada vuelta; interpolar cuadros no recrea la conducción real. "
    "La referencia es una vuelta observada, no un objetivo óptimo; diferencias no demuestran causas."
)


def _explanation(first, second, delta, first_lap, second_lap):
    if delta is None:
        return ("Falta cobertura temporal para medir cuánto cambió el tiempo en esta zona.",
                "Revisar la captura antes y después de la zona; comparar solo los canales observados.")
    reference = f"la referencia {first_lap}" if first_lap is not None else "la referencia"
    reviewed = f"la vuelta {second_lap}" if second_lap is not None else "la vuelta seleccionada"
    time = (f"En {reviewed} {'pierdes' if delta > 0 else 'ganas'} {abs(delta):.3f} s aquí"
            if abs(delta) > 0.000001 else f"El tiempo de {reviewed} coincide con {reference} aquí")
    entry = (second["speed_entry_kmh"] - first["speed_entry_kmh"]
             if all(s["speed_entry_kmh"] is not None for s in (first, second)) else None)
    exit_gain = (first["speed_exit_kmh"] - second["speed_exit_kmh"]
                 if all(s["speed_exit_kmh"] is not None for s in (first, second)) else None)
    action = (f"Reproduce en el mapa la preparación de {reference} y compara los mandos observados "
              "antes y después de la zona; contrasta el tiempo local antes de probar un cambio.")
    if exit_gain is not None and exit_gain > 2:
        observation = (f"En {reference} llegaste {entry:.1f} km/h más lento a esta zona, "
                       f"pero saliste {exit_gain:.1f} km/h más rápido. {time}."
                       if entry is not None and entry > 2 else
                       f"En {reference} saliste {exit_gain:.1f} km/h más rápido que en {reviewed}. {time}.")
        action = (f"Reproduce en el mapa la preparación de {reference} y compara cuándo frenas y vuelves al gas; "
                  "busca repetir su velocidad de salida antes de intentar entrar más rápido.")
    elif exit_gain is not None and exit_gain < -2:
        observation = (f"En {reviewed} saliste {-exit_gain:.1f} km/h más rápido que en {reference}. {time}.")
        action = (f"Compara en el mapa la preparación y la parte central de esta zona con {reference}; "
                  "revisa dónde cambia el tiempo antes de modificar una salida que ya es más rápida.")
    elif entry is not None and entry > 2:
        observation = f"En {reviewed} llegaste {entry:.1f} km/h más rápido que en {reference}. {time}."
    elif entry is not None and entry < -2:
        observation = f"En {reviewed} llegaste {-entry:.1f} km/h más lento que en {reference}. {time}."
    else:
        observation = f"{time}, frente a {reference}; la velocidad observada no muestra una diferencia mayor a 2 km/h en los extremos disponibles."
    missing = [label for key, label in (("speed_entry_kmh", "velocidad de entrada"),
                                        ("speed_exit_kmh", "velocidad de salida"),
                                        ("brake_mean_pct", "freno"), ("throttle_mean_pct", "gas"))
               if any(s[key] is None for s in (first, second))]
    if missing:
        if entry is None and exit_gain is None:
            observation = f"{time}, frente a {reference}."
        observation = observation.rstrip(".") + f"; faltan datos de {', '.join(missing)} y solo se comparan el tiempo y los canales observados."
        action = (f"Reproduce en el mapa la preparación de {reference} usando solo los canales disponibles; "
                  f"registra {', '.join(missing)} antes de sacar conclusiones sobre freno, gas o velocidad de salida.")
    return observation, action


def _frames(lap):
    start, duration = lap.start_time_s, lap.end_time_s - lap.start_time_s
    samples = [s for s in lap.samples if start < s.time_s < lap.end_time_s]
    stride = max(1, (len(samples) + 1197) // 1198)
    indices = set(range(0, len(samples), stride))
    # ponytail: 1.200 cuadros objetivo; los bordes de huecos pueden excederlo.
    # Si el tamaño lo exige, representar intervalos ausentes sin descartar evidencia.
    for index, (previous, current) in enumerate(zip(samples, samples[1:]), 1):
        if any((getattr(previous, name) is None) != (getattr(current, name) is None)
               for name in ("speed_mps", "brake", "throttle")):
            indices.update((index - 1, index))

    def controls(sample):
        return [None if getattr(sample, name) is None else getattr(sample, name) * scale
                for name, scale in (("speed_mps", 3.6), ("brake", 100), ("throttle", 100))]

    def boundary(progress, elapsed):
        values = [_interpolate(lap, progress, name) for name in ("speed_mps", "brake", "throttle")]
        return [elapsed, progress * 100, *[None if value is None else value * scale
                                          for value, scale in zip(values, (3.6, 100, 100))]]

    frames = [boundary(0, 0)]
    for index in sorted(indices):
        sample = samples[index]
        frames.append([sample.time_s - start, sample.lap_dist_pct * 100, *controls(sample)])
    frames.append([duration, 100, None, None, None])
    labels = Counter(s.lap for s in lap.samples if s.lap is not None).most_common(1)
    return {"lap": labels[0][0] if labels else None, "duration_s": duration, "frames": frames}


def corner_review(first, second):
    """Comparar regiones amplias; nunca asignar un tramo genérico a una curva exacta."""
    samples = first.samples + second.samples
    if (not first.complete or not second.complete or first.run_number != second.run_number
            or len({(s.source, s.session_id) for s in samples}) != 1):
        return {"available": False, "reason": "Se requieren dos vueltas completas de la misma sesión y bloque temporal."}
    if (not samples or any(s.track_id != 127 or s.track_config != "Full Course"
                           or s.track_length_m is None or abs(s.track_length_m - 4056.9) >= 41
                           for s in samples)):
        return {"available": False, "reason": "Mapa disponible solo para Road Atlanta 127, Full Course y longitud compatible conocida en ambas vueltas."}
    if any(s.car_id is None for s in samples) or len({s.car_id for s in samples}) != 1:
        return {"available": False, "reason": "Auto desconocido o distinto entre las vueltas; no se presenta comparación sobre el mapa."}
    from .race_results import _stats
    from .track_map import ROAD_ATLANTA_MAP

    replay = {"first": _frames(first), "second": _frames(second)}
    zones = []
    for zone_id, name, lo, hi in _ZONES:
        a, b = _stats(first, lo / 100, hi / 100), _stats(second, lo / 100, hi / 100)
        delta = None if a["duration_s"] is None or b["duration_s"] is None else b["duration_s"] - a["duration_s"]
        observation, next_test = _explanation(a, b, delta, replay["first"]["lap"], replay["second"]["lap"])
        zones.append({"id": zone_id, "name": name, "start_pct": lo, "end_pct": hi,
                      "delta_s": delta, "first": a, "second": b,
                      "observation": observation, "next_test": next_test})
    top = sorted((z for z in zones if z["delta_s"] is not None and z["delta_s"] > 0.000001),
                 key=lambda zone: zone["delta_s"], reverse=True)[:3]
    return {"available": True, "map": ROAD_ATLANTA_MAP, "zones": zones,
            "top_zone_ids": [z["id"] for z in top], "replay": replay, "note": _NOTE}
