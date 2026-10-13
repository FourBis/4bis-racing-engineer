"""Contrato persistido v1. Las unidades forman parte de los nombres."""
import math
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True, slots=True)
class Sample:
    source: str
    session_id: str
    time_s: float
    lap: int | None = None
    lap_dist_pct: float | None = None
    speed_mps: float | None = None
    brake: float | None = None
    throttle: float | None = None
    clutch: float | None = None
    steering_wheel_rad: float | None = None
    rpm: float | None = None
    gear: int | None = None
    fuel_l: float | None = None
    lap_time_s: float | None = None
    incidents: int | None = None
    on_track: bool | None = None
    is_replay: bool | None = None
    damage: str = "unknown"
    traffic: str = "unknown"
    brake_raw: float | None = None
    throttle_raw: float | None = None
    abs_active: bool | None = None
    vert_accel_mps2: float | None = None
    track_id: int | None = None
    track_name: str | None = None
    track_config: str | None = None
    track_length_m: float | None = None
    car_id: int | None = None
    car_name: str | None = None
    on_pit_road: bool | None = None
    # Raw driver-adjustment value; its meaning can vary by car. Never a pedal percentage.
    brake_bias: float | None = None
    brake_bias_unit: str | None = None

    def __post_init__(self):
        if self.source not in ("demo", "iracing"):
            raise ValueError("Origen desconocido; no se puede certificar la procedencia")
        if not isinstance(self.session_id, str) or not 1 <= len(self.session_id) <= 240:
            raise ValueError("session_id debe ser un texto de 1 a 240 caracteres")
        if self.damage not in ("healthy", "damaged", "unknown"):
            raise ValueError("Estado de daño inválido")
        if self.traffic not in ("clear", "traffic", "unknown"):
            raise ValueError("Estado de tráfico inválido")
        for name in ("on_track", "is_replay", "abs_active", "on_pit_road"):
            if getattr(self, name) is not None and type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} debe ser booleano o null")
        for name, lower, upper in (("lap", 0, 100000), ("gear", -1, 20), ("incidents", 0, 100000)):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not lower <= value <= upper):
                raise ValueError(f"{name} fuera de rango")
        for name in ("track_id", "car_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} debe ser un entero no negativo o null")
        for name, minimum, maximum in (("track_name", 1, 160), ("track_config", 0, 160),
                                       ("car_name", 1, 160), ("brake_bias_unit", 0, 32)):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not minimum <= len(value) <= maximum):
                raise ValueError(f"{name} debe tener entre {minimum} y {maximum} caracteres o ser null")
        for name in ("time_s", "lap_dist_pct", "speed_mps", "brake", "throttle", "clutch",
                     "steering_wheel_rad", "rpm", "fuel_l", "lap_time_s", "brake_raw",
                     "throttle_raw", "vert_accel_mps2", "track_length_m", "brake_bias"):
            value = getattr(self, name)
            if value is None and name != "time_s":
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} debe ser un número finito o null")
            if name in ("lap_dist_pct", "brake", "throttle", "clutch", "brake_raw", "throttle_raw") and not 0 <= value <= 1:
                raise ValueError(f"{name} requiere fracción entre 0 y 1")
            if name not in ("steering_wheel_rad", "vert_accel_mps2", "brake_bias") and value < 0:
                raise ValueError(f"{name} no puede ser negativo")
            if name == "track_length_m" and not 0 < value <= 1_000_000:
                raise ValueError("track_length_m debe ser mayor que 0 y hasta 1000000")

    def to_dict(self):
        return {"schema_version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, row):
        if not isinstance(row, dict):
            raise ValueError("Cada muestra debe ser un objeto")
        row = dict(row)
        version = row.pop("schema_version", None)
        if type(version) is not int or version != 1:
            raise ValueError("Versión de muestra incompatible; se requiere schema_version=1")
        unknown = set(row) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Campos desconocidos: {sorted(unknown)}")
        try:
            return cls(**row)
        except TypeError as error:
            raise ValueError("Muestra incompleta o inválida") from error
