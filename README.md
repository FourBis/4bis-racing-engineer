# 4bis Racing Engineer

MVP local de telemetría y coaching para iRacing: demo sintética, replay SQLite,
dashboard, lectura del SDK Windows y voz local opcional. Se recomienda Python
3.15; la compatibilidad mínima sigue siendo Python 3.11.
Demo, replay y tests usan la biblioteca estándar, sin cuenta ni API key.

## Iniciar

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe run.py demo
# Abrir http://127.0.0.1:8765
.\.venv\Scripts\python.exe check.py
```

La demo tiene tres vueltas sintéticas de 30 segundos, a velocidad x5. No
representa una combinación real de auto y pista. `--speed 1` reproduce en tiempo real.

```powershell
# Crear una grabación e informe exclusivamente sintéticos.
.\.venv\Scripts\python.exe run.py demo --headless --record recordings/demo.sqlite --report reports/demo
# Replay sin modificar el origen.
.\.venv\Scripts\python.exe run.py replay recordings/demo.sqlite
```

LIVE requiere Windows, iRacing y `pyirsdk`. La voz requiere además `pywin32` y una
voz española de Windows SAPI. Instalar solo lo que falte:

```powershell
$env:PYYAML_FORCE_LIBYAML = "0"
.\.venv\Scripts\python.exe -m pip install pyirsdk pywin32
.\.venv\Scripts\python.exe run.py live --port 8766
# Prueba breve del SDK, solo lectura:
.\.venv\Scripts\python.exe run.py live --headless --seconds 5
```

En Python 3.15, PyYAML 6.0.3 puede instalarse sin compilador C++ usando
`PYYAML_FORCE_LIBYAML=0`: selecciona el parser Python puro. En PowerShell,
`$env:` limita esta opción al proceso actual.

El panel indica conexión, datos obsoletos y canales ausentes. Iniciar el programa
no activa grabación ni voz. Usar **Iniciar grabación**, **Activar voz** o los
parámetros explícitos `--record RUTA` y `--voice`. **Silenciar voz** cancela la
locución. `--storage RUTA` elige el archivo del botón sin empezar a grabar.
Si una base tiene varias capturas, replay solicita `--recording-id ID`.

## Funciones y límites

- **En pista:** próxima curva identificada, indicación de frenada observada,
  delta y pedales grandes. **Análisis:** comparación, trazas, rebotes e historial
  de voz. Grabación y voz permanecen accesibles en ambas vistas.
- El comparador muestra solo vueltas completas elegibles con tiempo positivo;
  los segmentos excluidos se conservan en el informe. Los pedales sin lectura
  aparecen como `—`, sin convertirlos en cero.
- Trazas de freno, acelerador, dirección y velocidad; muestras con unidades,
  valores ausentes y procedencia. Grabación optativa y replay de solo lectura.
- Separación de vueltas, comparación por progreso y hallazgos con evidencia.
  Daño/tráfico desconocidos no se convierten en condiciones verificadas.
- Voz breve: ABS sostenido, cambio brusco de gas y guía de curvas, con prioridades,
  pausas y caducidad. El historial diferencia aceptación, omisión, inicio, fin y
  error; se persiste al grabar. Aceptación no certifica que el audio se escuchó.
- Revisión de aceleración vertical con marcas manuales; no mide recorrido de
  suspensión ni diagnostica piezas. Exportación JSON/Markdown.
- Servidor loopback con comprobaciones Host/Origin/CSRF para acciones locales.

La guía aprende hasta tres frenadas de una vuelta completa válida observada en la
misma sesión/auto/trazado. El mapa aproximado de Road Atlanta Full Course permite
asociarlas a curvas 1, 5, 6, 7 y 10a. Ejemplo:
**«Viene curva uno. Freno fuerte y suelta progresivo».**

Fuerte/media/suave clasifica el pico normalizado observado (umbrales 0,65/0,35),
no fuerza física ni una frenada óptima. «Soltar progresivo» es una propuesta de
práctica. Un dato ausente durante el episodio descarta su pico. Sin mapa compatible
o curva identificable, no se inventa un aviso numerado.

Los avisos anticipan aproximadamente 4–6 segundos al inicio observado de frenada,
calculados con distancia y velocidad actual. Requieren zona estable, datos frescos
y voz activada en LIVE. Demo/replay no emiten consejos automáticos de conducción.
Las posiciones deben comprobarse conduciendo.

El tráfico por sí solo no silencia la guía: el piloto adapta su ejecución. Un delta
no demuestra un error de técnica. La guía objetivo validada y persistente por curva
sigue pendiente; hoy otra vuelta válida más rápida puede cambiar la referencia.
No se prometen mejoras de tiempo.

## Privacidad y desarrollo

### Balance al terminar una tanda

En **Análisis → Balance de vueltas**, el panel compara la referencia más rápida
sin nuevos incidentes con la vuelta completa más lenta analizable. También permite
seleccionar otra vuelta: muestra diferencia de tiempo, trazas por progreso y los
tres tramos con mayor pérdida, junto con velocidad, gas, freno y tiempo con ABS.
El JSON del balance y el informe Markdown incluyen los motivos de exclusión.

Se conserva la vuelta con errores para poder revisarla. Se excluyen vueltas
incompletas, replay, datos discontinuos, detenciones sostenidas y pasos por boxes
reportados por `OnPitRoad`. Grabaciones antiguas sin ese canal pueden contener un
paso por boxes en movimiento no identificable; el panel lo advierte. Daño y tráfico
desconocidos permiten una revisión descriptiva, sin asumir condiciones limpias.
Una referencia con daño explícito se descarta. Este balance es independiente del
comparador estricto, que conserva sus criterios originales.

El balance usa el último bloque de sesión y la misma identidad de auto/pista/layout.
Las pérdidas se ubican en tramos del 5% de vuelta, todavía sin asignación a curvas.
ABS o un cambio de pedal no prueban bloqueo, patinaje ni la causa del tiempo perdido.
La selección manual queda fijada hasta volver a Automática o cambiar de sesión.

### Desarrollo local

Solo código, tests sintéticos y documentación técnica. Fotos, videos, grabaciones
reales, informes de sesiones y contexto privado quedan fuera de Git. No se envía
telemetría ni se modifican ajustes del juego o automatiza conducción. Sin APIs de IA.

La aprobación de la suite no acredita utilidad en pista ni audibilidad.
Para desarrollar la interfaz, ejecutar también `node --test tests/dashboard.test.cjs`
con Node.js; usa su runner nativo y no requiere instalar paquetes.
En Python 3.15 se verificaron 121 tests y demo/replay con 901 muestras;
las regresiones de interfaz usan 10 tests nativos de Node.js.
El SDK inició limpio pero sin simulador conectado (0 muestras); SAPI indicó
`ready`, lo que no confirma audio oído ni validación conduciendo.
Ver [arquitectura](docs/ARCHITECTURE.md), [canales SDK](docs/SDK_CHANNELS.md),
[mapa](docs/TRACK_MAP.md) y [mejoras propuestas](docs/ROADMAP.md).

La rama principal es `develop`; se integran únicamente PR de JeremiasBadilla
con las comprobaciones obligatorias aprobadas. Ver [reglas de contribución](CONTRIBUTING.md).

[Ejecuciones de CI](https://github.com/FourBis/4bis-racing-engineer/actions/workflows/ci.yml)
· [Reglas activas de la principal](https://github.com/FourBis/4bis-racing-engineer/rules)
