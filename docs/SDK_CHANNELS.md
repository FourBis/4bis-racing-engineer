# Canales iRacing usados por el adaptador

El adaptador importa `pyirsdk` de forma opcional y lee su interfaz local de
telemetría. `is_connected` se consulta como propiedad booleana; `startup`,
`freeze_var_buffer_latest`, `unfreeze_var_buffer_latest` y `shutdown` provienen
de la API de `pyirsdk` 1.3.6.
La implementación local se verificó sin iniciar el simulador. En ejecución,
`var_headers_names` y el encabezado de cada variable (`unit`, `desc`) determinan
la disponibilidad y los metadatos concretos de esa sesión. `channels` expone
`available`, `valid` e `issue`; una unidad live desconocida deshabilita la
lectura de ese campo. Si el header live no está disponible, se conserva el
valor respaldado por documentación y se marca la unidad como no confirmada en
esa sesión. Los valores opcionales inválidos o sentinels (por ejemplo
`LapDistPct=-1` fuera de pista) se convierten a `None` con un `issue`, sin
cancelar la muestra.

| Canal | Unidad documentada | Uso | Si falta |
| --- | --- | --- | --- |
| `SessionTime` | s | reloj de sesión y eje temporal | no se produce muestra |
| `Lap` | vueltas | vuelta iniciada | `None` |
| `LapDistPct` | proporción 0–1 | posición normalizada en la vuelta | `None` |
| `Speed` | m/s | velocidad GPS | `None` |
| `Brake` | proporción 0–1 | mando de freno procesado por el simulador | `None` |
| `BrakeRaw` | proporción 0–1 | entrada bruta del mando de freno | `brake_raw=None` |
| `Throttle` | proporción 0–1 | mando de acelerador procesado por el simulador | `None` |
| `ThrottleRaw` | proporción 0–1 | entrada bruta del acelerador del simulador; no lectura eléctrica del Hall | `None` |
| `BrakeABSactive` | booleano | ABS reduciendo presión; no prueba bloqueo de rueda | `None` |
| `VertAccel` | m/s², incluida gravedad | filtro conservador de golpes para consejos de gas | `None` |
| `Clutch` | proporción 0–1 | mando de embrague procesado por el simulador | `None` |
| `SteeringWheelAngle` | rad | ángulo del volante | `None` |
| `RPM` | rev/min | RPM de motor | `None` |
| `Gear` | marcha | -1 reversa, 0 neutro | `None` |
| `FuelLevel` | l | combustible restante | `None` |
| `LapCurrentLapTime` | s | estimación del tiempo de vuelta actual | `None` |
| `PlayerCarMyIncidentCount` | conteo | incidentes propios de la sesión | `None` |
| `IsOnTrack` | booleano | física de pista activa con jugador en el auto | `None` |
| `IsReplayPlaying` | booleano | reproducción activa | `None` |

Identidad de sesión: `WeekendInfo.SessionID` y `WeekendInfo.SubSessionID`,
`SessionNum` y `SessionUniqueID` si existen. Una reconexión, cambio de identidad
o retroceso de `SessionTime` inicia un segmento nuevo. Si no llegan ticks por
más de dos segundos, `status` indica telemetría obsoleta. Al detectar
desconexión se cierra el mapping y se reinicia el SDK antes de reintentar, para
permitir adjuntarse a un proceso nuevo. Los canales ausentes permanecen nulos;
`Brake`/`BrakeRaw` son entradas normalizadas, no presión hidráulica ni fuerza
física del pedal. El adaptador no solicita ni envía comandos broadcast.

## Referencias verificadas

- Código de `pyirsdk` 1.3.6 (`irsdk.py`);
  confirma propiedad `is_connected`, acceso `sdk[name]`, ciclo de vida y
  congelado de buffers. Los encabezados live proporcionan metadatos de unidad,
  descripción y disponibilidad.
- Índice de variables `pyirsdk` (`vars.txt`):
  [kutu/pyirsdk vars.txt](https://github.com/kutu/pyirsdk/blob/master/vars.txt).
  Documenta nombres y unidades de los canales usados, incluido `BrakeRaw`,
  `Speed`, `FuelLevel`, `SteeringWheelAngle` e `IsOnTrack`.
- Descripción de la telemetría local del SDK:
  [iRacing SDK Documentation — Telemetry](https://sajax.github.io/irsdkdocs/telemetry/).

`dcABS` y `dcTractionControl` son ajustes, no indicadores de intervención.
El adaptador no usa una bandera de TC activo, por lo que no anuncia patinaje.
Las grabaciones v1 anteriores siguen siendo legibles: los campos opcionales nuevos
son `None` cuando no estaban guardados; no se rellenan con ceros.
