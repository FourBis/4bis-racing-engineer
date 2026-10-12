"""Circuito artificial de tres vueltas; no representa el Cayman ni el GR86."""
from math import pi, sin

from .model import Sample


def demo_samples(laps=3, hz=10):
    # ponytail: circuito analítico para regresión; usar una captura autorizada para validar conducción.
    lap_seconds = 30
    for index in range(laps * lap_seconds * hz + 1):
        t = index / hz
        lap = int(t // lap_seconds) + 1
        progress = (t % lap_seconds) / lap_seconds
        phase = t % 10
        brake = max(0.0, 0.85 * (1 - abs(phase - 2) / 1.1))
        throttle = max(0.0, min(1.0, (phase - 2.7) / 1.5)) if phase < 5 else 1.0
        if phase < 0.8:
            throttle = 1.0
        # Un accionamiento rápido sostenido es deliberado en el fixture, no una avería.
        if lap == 2 and 11 <= t % lap_seconds < 11.4:
            brake = 0.72
            throttle = 0.0
        if lap == 2 and 17 <= t % lap_seconds < 19:
            brake = 0.06
        speed = 35 + 14 * sin((phase - 4) * pi / 5) + (lap - 1) * 0.5
        yield Sample(source="demo", session_id="synthetic-circuit-v1", time_s=t,
                     lap=lap, lap_dist_pct=progress, speed_mps=speed, brake=brake,
                     throttle=throttle, clutch=0.0, steering_wheel_rad=sin(phase * pi / 5) * 0.5,
                     rpm=3000 + speed * 65, gear=3 if speed < 38 else 4,
                     fuel_l=20 - t * 0.025, lap_time_s=t % lap_seconds,
                     incidents=0, on_track=True, is_replay=False, damage="healthy", traffic="clear",
                     brake_raw=None, track_id=0, track_name="Synthetic test circuit",
                     track_config="Synthetic layout", track_length_m=1500.0,
                     car_id=0, car_name="Synthetic demo car")
