# 4bis Racing Engineer

MVP local de telemetría y coaching para iRacing: demo sintética, replay SQLite,
dashboard, lectura del SDK Windows y voz local opcional. Python 3.11+.
Demo, replay y tests usan la biblioteca estándar, sin cuenta ni API key.

## Iniciar

```powershell
python run.py demo
# Abrir http://127.0.0.1:8765
python check.py
```

La demo tiene tres vueltas sintéticas de 30 segundos, a velocidad x5. No
representa una combinación real de auto y pista. `--speed 1` reproduce en tiempo real.

```powershell
# Crear una grabación e informe exclusivamente sintéticos.
python run.py demo --headless --record recordings/demo.sqlite --report reports/demo
# Replay sin modificar el origen.
python run.py replay recordings/demo.sqlite
```

LIVE requiere Windows, iRacing y `pyirsdk`. La voz requiere además `pywin32` y una
voz española de Windows SAPI. Instalar solo lo que falte:

```powershell
python -m pip install pyirsdk pywin32
python run.py live --port 8766
# Prueba breve del SDK, solo lectura:
python run.py live --headless --seconds 5
```

El panel indica conexión, datos obsoletos y canales ausentes. Iniciar el programa
no activa grabación ni voz. Usar **Iniciar grabación**, **Activar voz** o los
parámetros explícitos `--record RUTA` y `--voice`. **Silenciar voz** cancela la
locución. `--storage RUTA` elige el archivo del botón sin empezar a grabar.
Si una base tiene varias capturas, replay solicita `--recording-id ID`.

## Funciones y límites

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

Solo código, tests sintéticos y documentación técnica. Fotos, videos, grabaciones
reales, informes de sesiones y contexto privado quedan fuera de Git. No se envía
telemetría ni se modifican ajustes del juego o automatiza conducción. Sin APIs de IA.

La aprobación de la suite no acredita utilidad en pista ni audibilidad.
Ver [arquitectura](docs/ARCHITECTURE.md), [canales SDK](docs/SDK_CHANNELS.md),
[mapa](docs/TRACK_MAP.md) y [mejoras propuestas](docs/ROADMAP.md).

La rama principal es `develop`; se integran únicamente PR de JeremiasBadilla
con las comprobaciones obligatorias aprobadas. Ver [reglas de contribución](CONTRIBUTING.md).
