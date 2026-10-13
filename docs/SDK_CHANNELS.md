# Canales iRacing usados por el adaptador

El adaptador importa `pyirsdk` de forma opcional y lee su interfaz local de
telemetría. `is_connected` se consulta como propiedad booleana; `startup`,
`freeze_var_buffer_latest`, `unfreeze_var_buffer_latest` y `shutdown` provienen
de la API de `pyirsdk` 1.3.6.
La implementación local se verificó sin iniciar el simulador. En ejecución,
`var_headers_names` y el encabezado de cada variable (`unit`, `desc`) determinan
la disponibilidad y los metadatos concretos de esa sesión. `channels` expone
`available`, `valid` e `issue`; una unidad live desconocida deshabilita la
lectura de ese campo, salvo el ajuste opaco `dcBrakeBias` descrito abajo.
Si el header live no está disponible, se conserva el
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
| `dcBrakeBias` | ajuste dependiente del auto; unidad live preservada, incluso vacía | contexto de configuración, sin conversión a pedal o porcentaje delantero | `brake_bias=None`, `brake_bias_unit=None` |
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
| `OnPitRoad` | booleano | auto en el carril de boxes entre los conos; excluye esa vuelta del balance | `None` |
| `IsReplayPlaying` | booleano | reproducción activa | `None` |

Identidad de sesión: `WeekendInfo.SessionID` y `WeekendInfo.SubSessionID`,
`SessionNum` y `SessionUniqueID` si existen. Una reconexión, cambio de identidad
o retroceso de `SessionTime` inicia un segmento nuevo. Si no llegan ticks por
más de dos segundos, `status` indica telemetría obsoleta. Al detectar
desconexión se cierra el mapping y se reinicia el SDK antes de reintentar, para
permitir adjuntarse a un proceso nuevo. Los canales ausentes permanecen nulos;
`Brake`/`BrakeRaw` son entradas normalizadas, no presión hidráulica ni fuerza
física del pedal. El adaptador no solicita ni envía comandos broadcast.

## Velocidad y ajuste de brake bias

`Speed` se conserva en `speed_mps` como m/s medidos por el simulador; mostrar
km/h usa la conversión de unidad ×3,6. La velocidad actual al emitir un aviso y
la velocidad observada antes de iniciar una frenada son instantes distintos.
Una referencia necesita comparar velocidades en la misma posición de aproximación;
no se deriva una nueva distancia o porcentaje de pedal con una fórmula universal.

`dcBrakeBias` se guarda sin escalar en `brake_bias`: un número finito que puede
ser firmado. `brake_bias_unit` conserva la unidad del encabezado live recortada
(hasta 32 caracteres): `""` es una unidad explícitamente vacía; `None` significa
encabezado/unidad ausente o inválida. Si falta el valor, ambos campos son `None`.
No se convierte 0,565 en 56,5 ni se limita el ajuste a 0–100. Un valor negativo
puede ser un ajuste del auto; este adaptador no interpreta su escala ni afirma
que sea porcentaje de reparto delantero. Una unidad desconocida permite conservar
la lectura bruta y deja un aviso en `channels`; `valid=True` sólo confirma un
valor numérico finito, no su interpretación física.

La disponibilidad se comprueba en cada sesión, no se garantiza para todos los
autos. El índice actual de pyirsdk no enumera `dcBrakeBias` para toda combinación.
La captura de metadatos publicada por node-irsdk lo describe como ajuste del auto,
tipo float y unidad vacía. Por eso comparar este contexto exige el mismo auto y
unidad; un ajuste igual tampoco certifica que el resto del setup, neumáticos,
clima o grip sean iguales. Un cambio requiere volver a observar una referencia,
sin aplicar una proporción lineal al pedal. Si no se dispone del canal, se informa
la limitación. Este incremento no añade configuración manual ni afirma conocerla.

Los campos opcionales se guardan en el JSON de muestras v1. Una captura anterior
sin ellos se reproduce con `None`, sin alterar la base ni inferir un ajuste desde
el nombre del auto, otra vuelta o una configuración por defecto.

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
- La incorporación oficial del canal de ajuste se documentó en
  [iRacing, notas de 2012 Season 3](https://www.iracing.com/release-notes-for-2012-season-3/).
  Esta fuente confirma el canal, no una unidad o interpretación universal por auto.
- Metadatos SDK publicados por el autor de la implementación node-irsdk:
  [telemetry-desc.json, dcBrakeBias](https://github.com/apihlaja/node-irsdk/blob/master/sample-data/telemetry-desc.json#L1044-L1049).
  La captura muestra `unit: ""`; no sustituye verificar el encabezado de la sesión
  ni un manual específico del auto. No se ha validado este nuevo canal conectado
  al simulador ni conduciendo.

`dcABS` y `dcTractionControl` son ajustes, no indicadores de intervención.
El adaptador no usa una bandera de TC activo, por lo que no anuncia patinaje.
`OnPitRoad` se verificó en el índice de variables de pyirsdk; también se valida
su encabezado y tipo al conectar. Las grabaciones anteriores conservan `None`:
en ellas una parada sostenida se identifica como detención, no como boxes
confirmados. Una pasada por boxes sin detenerse puede no ser identificable.
Las grabaciones v1 anteriores siguen siendo legibles: los campos opcionales nuevos
son `None` cuando no estaban guardados; no se rellenan con ceros.
