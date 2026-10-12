"""Adaptador local de solo lectura para la telemetría live de iRacing."""

from __future__ import annotations

import math
import os
import re
import time
from typing import Any

from .model import Sample

_CHANNELS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "SessionTime": ("s", "Tiempo transcurrido de sesión", ("s",)),
    "Lap": ("lap", "Número de vuelta iniciada", ("",)),
    "LapDistPct": ("% (0–1)", "Progreso normalizado de la vuelta", ("%",)),
    "Speed": ("m/s", "Velocidad GPS del vehículo", ("m/s",)),
    "Brake": ("% (0–1)", "Mando de freno procesado por el simulador", ("%",)),
    "BrakeRaw": ("% (0–1)", "Entrada bruta del mando de freno", ("%",)),
    "Throttle": ("% (0–1)", "Mando de acelerador procesado por el simulador", ("%",)),
    "ThrottleRaw": ("% (0–1)", "Entrada bruta del acelerador del simulador", ("%",)),
    "BrakeABSactive": ("bool", "ABS reduciendo presión de freno; no confirma bloqueo de rueda", ("",)),
    "VertAccel": ("m/s^2", "Aceleración vertical incluida gravedad", ("m/s^2",)),
    "Clutch": ("% (0–1)", "Mando de embrague procesado por el simulador", ("%",)),
    "SteeringWheelAngle": ("rad", "Ángulo de volante", ("rad",)),
    "RPM": ("rev/min", "RPM del motor del jugador", ("revs/min",)),
    "Gear": ("gear", "Marcha actual; -1 reversa, 0 neutro", ("",)),
    "FuelLevel": ("l", "Combustible restante", ("l",)),
    "LapCurrentLapTime": ("s", "Estimación del tiempo de vuelta actual", ("s",)),
    "PlayerCarMyIncidentCount": ("incidents", "Incidentes propios de la sesión", ("",)),
    "IsOnTrack": ("bool", "Física de pista activa con el jugador dentro del auto", ("",)),
    "IsReplayPlaying": ("bool", "Reproducción activa", ("",)),
}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _metadata_integer(value: Any) -> int | None:
    parsed = _integer(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _metadata_text(value: Any, *, allow_empty: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > 160 or (not allow_empty and not value):
        return None
    return value


def _track_length_m(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(m|km)\s*", value, re.IGNORECASE)
    if not match:
        return None
    distance = _finite(float(match.group(1)))
    if distance is None:
        return None
    distance *= 1000 if match.group(2).lower() == "km" else 1
    return distance if 0 < distance <= 1_000_000 else None


class IRacingSource:
    """Lee muestras del SDK local; jamás envía comandos al simulador."""

    def __init__(self, sdk: Any = None, *, clock=time.monotonic) -> None:
        self.error: str | None = None
        self._sdk = sdk
        self._started = False
        self._clock = clock
        self._connected = False
        self._segment = 0
        self._base_session: str | None = None
        self._last_tick: Any = None
        self._last_session_time: float | None = None
        self._last_sample_at: float | None = None
        self._connected_at: float | None = None
        self._channels: dict[str, dict[str, Any]] = {
            name: {"unit": unit, "desc": desc, "available": False,
                   "valid": None, "issue": "sin conexión"}
            for name, (unit, desc, _accepted_units) in _CHANNELS.items()
        }
        if self._sdk is None and os.name == "nt":
            try:
                import irsdk

                self._sdk = irsdk.IRSDK()
            except Exception as exc:  # SDK import failures are reported through status.
                self.error = f"No se pudo cargar pyirsdk: {exc}"

    @property
    def channels(self) -> dict[str, dict[str, Any]]:
        return {name: details.copy() for name, details in self._channels.items()}

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def status(self) -> str:
        if self.error:
            return f"Error del adaptador iRacing: {self.error}"
        if not self._sdk:
            return "iRacing desconectado: el SDK solo está disponible en Windows"
        last_seen = self._last_sample_at if self._last_sample_at is not None else self._connected_at
        if self._connected and last_seen is not None and self._clock() - last_seen > 2.0:
            return "iRacing conectado; telemetría obsoleta (sin ticks nuevos por más de 2 s)"
        return "iRacing conectado" if self._connected else "iRacing desconectado"

    def _connected_now(self) -> bool:
        try:
            return bool(getattr(self._sdk, "is_connected", False))
        except Exception as exc:
            self.error = f"No se pudo consultar la conexión del SDK: {exc}"
            return False

    def _start(self) -> bool:
        if not self._sdk:
            return False
        if self._started:
            return True
        try:
            started = self._sdk.startup()
            self._started = bool(started)
            if not self._started:
                self._shutdown_sdk()
            return self._started
        except Exception as exc:
            self.error = f"No se pudo iniciar la lectura del SDK: {exc}"
            self._shutdown_sdk()
            return False

    def _reset_channels(self, issue: str = "sin conexión") -> None:
        for info in self._channels.values():
            info.update(available=False, valid=None, issue=issue)

    def _shutdown_sdk(self) -> None:
        if self._sdk and self._started:
            try:
                self._sdk.shutdown()
            except Exception as exc:
                self.error = f"No se pudo reiniciar el SDK: {exc}"
        self._started = False

    def _read(self, name: str) -> Any:
        if not self._sdk:
            return None
        try:
            return self._sdk[name]
        except (KeyError, TypeError, AttributeError, IndexError):
            return None
        except Exception as exc:
            self.error = f"Fallo al leer {name}: {exc}"
            return None

    def _available_names(self) -> set[str]:
        names = getattr(self._sdk, "var_headers_names", None) if self._sdk else None
        if names is None:
            names = getattr(self._sdk, "available_channels", None) if self._sdk else None
        return set(names or ())

    def _update_channel_metadata(self, names: set[str]) -> set[str]:
        headers: dict[str, Any] = {}
        try:
            headers = getattr(self._sdk, "_var_headers_dict", {}) or {}
        except Exception:
            pass
        readable: set[str] = set()
        for name, info in self._channels.items():
            header = headers.get(name)
            exists = name in names if names else header is not None
            info.update(available=exists, valid=exists, issue=None if exists else "canal no disponible en esta sesión")
            if exists:
                readable.add(name)
            if header is not None:
                unit = getattr(header, "unit", None)
                desc = getattr(header, "desc", None)
                accepted = _CHANNELS[name][2]
                if unit is not None and str(unit).strip().lower() not in accepted:
                    info.update(available=False, valid=False,
                                issue=f"unidad del SDK no reconocida: {unit!r}")
                    readable.discard(name)
                elif unit:
                    info["unit"] = unit
                elif exists:
                    info["valid"] = None
                    info["issue"] = "unidad no expuesta por header live; se usa la referencia documentada"
                if desc:
                    info["desc"] = desc
            elif exists:
                info["valid"] = None
                info["issue"] = "header live no disponible; unidad respaldada por documentación, no por esta sesión"
        return readable

    def _mark_invalid_value(self, name: str, issue: str) -> None:
        self._channels[name].update(valid=False, issue=issue)

    def _optional(self, name: str, value: Any, *, minimum: float | None = None,
                  maximum: float | None = None, integer: bool = False,
                  boolean: bool = False) -> Any:
        if value is None:
            if not self._channels[name]["available"]:
                return None
            self._mark_invalid_value(name, "valor ausente en este tick")
            return None
        if boolean:
            if isinstance(value, bool):
                return value
            self._mark_invalid_value(name, "valor no booleano")
            return None
        if integer:
            parsed = _integer(value)
            if parsed is None:
                self._mark_invalid_value(name, "valor no entero")
                return None
            number = parsed
        else:
            number = _finite(value)
            if number is None:
                self._mark_invalid_value(name, "valor no finito o de tipo inválido")
                return None
        if minimum is not None and number < minimum or maximum is not None and number > maximum:
            self._mark_invalid_value(name, f"valor fuera de rango esperado [{minimum}, {maximum}]")
            return None
        return number

    def _session_identity(self, session_num: int | None) -> str:
        weekend = self._read("WeekendInfo")
        weekend = weekend if isinstance(weekend, dict) else {}
        session_id = weekend.get("SessionID")
        subsession_id = weekend.get("SubSessionID")
        session_unique_id = self._read("SessionUniqueID")
        components = []
        if session_id is not None:
            components.append(f"session={session_id}")
        if subsession_id is not None:
            components.append(f"subsession={subsession_id}")
        if session_num is not None:
            components.append(f"num={session_num}")
        if session_unique_id is not None:
            components.append(f"unique={session_unique_id}")
        return "/".join(components) or "session=unknown"

    def _track_metadata(self) -> dict[str, Any]:
        weekend = self._read("WeekendInfo")
        if not isinstance(weekend, dict):
            weekend = {}
        display_name = _metadata_text(weekend.get("TrackDisplayName"))
        track_name = display_name or _metadata_text(weekend.get("TrackName"))
        return {
            "track_id": _metadata_integer(weekend.get("TrackID")),
            "track_name": track_name,
            "track_config": _metadata_text(weekend.get("TrackConfigName"), allow_empty=True),
            "track_length_m": _track_length_m(weekend.get("TrackLength")),
        }

    def _car_metadata(self) -> dict[str, Any]:
        driver_info = self._read("DriverInfo")
        drivers = driver_info.get("Drivers") if isinstance(driver_info, dict) else None
        if not isinstance(drivers, list):
            return {"car_id": None, "car_name": None}
        # PlayerCarIdx is the live telemetry index; DriverCarIdx is the session-string fallback.
        player_car_idx = _metadata_integer(self._read("PlayerCarIdx"))
        if player_car_idx is None:
            player_car_idx = _metadata_integer(driver_info.get("DriverCarIdx"))
        if player_car_idx is None:
            return {"car_id": None, "car_name": None}
        driver = next((item for item in drivers if isinstance(item, dict)
                       and _metadata_integer(item.get("CarIdx")) == player_car_idx), None)
        if driver is None:
            return {"car_id": None, "car_name": None}
        return {
            "car_id": _metadata_integer(driver.get("CarID")),
            "car_name": _metadata_text(driver.get("CarScreenName")),
        }

    def _tick(self, fallback: tuple[Any, ...]) -> Any:
        # pyirsdk 1.3.6 congela una copia estable en _var_buffer_latest;
        # tick_count permite detectar un simulador conectado pero detenido.
        try:
            buffer = getattr(self._sdk, "_var_buffer_latest", None)
            tick = getattr(buffer, "tick_count", None)
            if tick is not None:
                return ("sdk", tick)
        except Exception:
            pass
        return ("sample", fallback)

    def read(self) -> Sample | None:
        """Entrega solo ticks nuevos; la desconexión y tick repetido son normales."""
        if not self._sdk or not self._start():
            self._connected = False
            self._reset_channels("SDK desconectado o startup fallido")
            return None

        connected = self._connected_now()
        if not connected:
            if self._connected:
                self._segment += 1
            self._connected = False
            self._last_tick = None
            self._base_session = None
            self._last_session_time = None
            self._last_sample_at = None
            self._connected_at = None
            self._reset_channels()
            self._shutdown_sdk()
            return None

        if not self._connected:
            self._segment += 1
            self._last_tick = None
            self._base_session = None
            self._last_session_time = None
            self._connected_at = self._clock()
        self._connected = True

        try:
            freeze = getattr(self._sdk, "freeze_var_buffer_latest", None)
            if callable(freeze):
                freeze()
            names = self._available_names()
            readable = self._update_channel_metadata(names)

            # Usar solo canales anunciados por el SDK cuando hay metadata.
            def get(name: str) -> Any:
                return self._read(name) if not names or name in readable else None

            session_time = _finite(get("SessionTime"))
            if session_time is None or session_time < 0:
                return None
            lap = self._optional("Lap", get("Lap"), minimum=0, integer=True)
            lap_dist = self._optional("LapDistPct", get("LapDistPct"), minimum=0, maximum=1)
            fallback = (session_time, lap, lap_dist)
            tick = self._tick(fallback)
            if tick == self._last_tick:
                return None

            session_num = _integer(self._read("SessionNum"))
            base_session = self._session_identity(session_num)
            if self._base_session is not None and base_session != self._base_session:
                self._segment += 1
            elif (self._base_session is not None and self._last_session_time is not None
                  and session_time < self._last_session_time):
                self._segment += 1

            sid = f"{base_session}:segment={self._segment}"
            sample = Sample(
                source="iracing",
                session_id=sid,
                time_s=session_time,
                lap=lap,
                lap_dist_pct=lap_dist,
                speed_mps=self._optional("Speed", get("Speed"), minimum=0),
                brake=self._optional("Brake", get("Brake"), minimum=0, maximum=1),
                throttle=self._optional("Throttle", get("Throttle"), minimum=0, maximum=1),
                clutch=self._optional("Clutch", get("Clutch"), minimum=0, maximum=1),
                steering_wheel_rad=self._optional("SteeringWheelAngle", get("SteeringWheelAngle")),
                rpm=self._optional("RPM", get("RPM"), minimum=0),
                gear=self._optional("Gear", get("Gear"), minimum=-1, maximum=20, integer=True),
                fuel_l=self._optional("FuelLevel", get("FuelLevel"), minimum=0),
                lap_time_s=self._optional("LapCurrentLapTime", get("LapCurrentLapTime"), minimum=0),
                incidents=self._optional("PlayerCarMyIncidentCount", get("PlayerCarMyIncidentCount"),
                                         minimum=0, integer=True),
                on_track=self._optional("IsOnTrack", get("IsOnTrack"), boolean=True),
                is_replay=self._optional("IsReplayPlaying", get("IsReplayPlaying"), boolean=True),
                brake_raw=self._optional("BrakeRaw", get("BrakeRaw"), minimum=0, maximum=1),
                throttle_raw=self._optional("ThrottleRaw", get("ThrottleRaw"), minimum=0, maximum=1),
                abs_active=self._optional("BrakeABSactive", get("BrakeABSactive"), boolean=True),
                vert_accel_mps2=self._optional("VertAccel", get("VertAccel")),
                **self._track_metadata(),
                **self._car_metadata(),
            )
            self._base_session = base_session
            self._last_tick = tick
            self._last_session_time = session_time
            self._last_sample_at = self._clock()
            self.error = None
            return sample
        except Exception as exc:
            self.error = f"No se pudo construir una muestra iRacing: {exc}"
            return None
        finally:
            try:
                unfreeze = getattr(self._sdk, "unfreeze_var_buffer_latest", None)
                if callable(unfreeze):
                    unfreeze()
            except Exception:
                pass

    def close(self) -> None:
        self._shutdown_sdk()
        self._connected = False
        self._last_tick = None
        self._last_sample_at = None
        self._connected_at = None
        self._reset_channels()
