"""Nombres de curvas y propuestas breves; no certifica una frenada óptima."""

# Proyección de rótulos sobre los dos bordes del SVG oficial, relativa a meta.
# Fuente, método y límites: docs/TRACK_MAP.md.
_ROAD_ATLANTA = (
    ("1", .130083), ("2", .185250), ("3", .207167), ("4", .239167),
    ("5", .359667), ("6", .489458), ("7", .518500), ("8", .589417),
    ("9", .760458), ("10a", .843208), ("10b", .868042),
    ("11", .908250), ("12", .966542),
)
_SPOKEN = {"1": "uno", "5": "cinco", "6": "seis", "7": "siete", "10a": "diez A"}
_INTENSITY = {"fuerte": "fuerte", "media": "medio", "suave": "suave"}


def _identify(point: dict, supported: bool) -> dict:
    result = {**point, "corner_name": None, "cue": None, "location_basis": "unknown",
              "target_validated": False, "instruction_basis": "practice_proposal"}
    progress = point.get("lap_dist_pct")
    if not supported or progress is None:
        return result
    # ponytail: asociación conservadora a cinco curvas; ampliar tras validar el
    # mapa contra SDK/video. Los rótulos del SVG no certifican un ápice o frenada.
    for index, (name, location) in enumerate(_ROAD_ATLANTA):
        if location < progress:
            continue
        previous = _ROAD_ATLANTA[index - 1][1]
        if (name not in _SPOKEN or not .008 <= location - progress <= .08
                or (progress - previous) % 1 < .008):
            break
        result.update(corner_name=f"Curva {name}", location_basis="official_map_estimate")
        intensity = _INTENSITY.get(point.get("initial_brake_class"))
        if intensity and point.get("reference_brake_peak") is not None:
            result["cue"] = f"Viene curva {_SPOKEN[name]}. Freno {intensity} y suelta progresivo."
        break
    return result


def corner_guidance(navigation: dict) -> dict:
    """Enrich a fresh snapshot without rewriting samples or manual kerb marks."""
    track = navigation["track"]
    length = track.get("length_m")
    supported = (track.get("track_id") == 127 and track.get("layout") == "Full Course"
                 and length is not None and abs(length - 4056.9) < 41)
    result = {**navigation, "zones": [_identify(z, supported) for z in navigation["zones"]],
              "next": _identify(navigation["next"], supported) if navigation["next"] else None,
              "map_note": (
                  "Road Atlanta Full Course: nombres del mapa oficial; ubicaciones aproximadas, "
                  "pendientes de comprobar conduciendo. Intensidad tomada de tu vuelta observada; "
                  "soltar progresivo es una propuesta de práctica, no un objetivo validado."
                  if supported else "Sin mapa compatible identificado: faltan nombres de curvas para la guía de voz.")}
    if not result["next"] or not result["next"]["cue"]:
        result.update(voice_ready=False, voice_reason="Guía de voz en espera de una curva identificada.")
    return result
