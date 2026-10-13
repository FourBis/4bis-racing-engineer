# Referencia de frenada del simulador

La guía describe hasta tres episodios de la vuelta personal completa más rápida
observada en el contexto actual. No acredita una vuelta óptima ni un objetivo
validado. No controla iRacing ni sirve como orientación para conducción vial.

## Qué se mide y qué se estima

| Dato | Interpretación |
| --- | --- |
| `Brake` | Mando normalizado 0–1 procesado por el simulador, no fuerza física ni presión hidráulica. |
| `reference_brake_peak_pct` | Máximo del episodio, presentado al múltiplo de 5 puntos porcentuales más cercano; no es presión inicial ni rango estadístico. |
| `braking_start_m` | Intervalo desde la última muestra ≤5% a la primera ≥15% de Brake, multiplicado por longitud SDK y redondeado hacia afuera a 10 m. Ancla: línea de meta, dirección de marcha. |
| `distance_m` | Distancia hacia adelante desde la posición actual hasta el umbral observado de frenada, con vuelta modular. No es distancia hasta curva/ápice ni cartel físico. |
| `reference_approach_speed_kph` | Velocidad medida en la última muestra ≤5%, anterior al cruce de 15%; solo se admite una rampa de hasta 0,5 s para el consejo numérico. |
| `matched_reference_speed_kph` | Velocidad de la referencia interpolada al progreso actual, sin atravesar huecos. Se compara con la velocidad actual medida; no con una velocidad futura. |
| `brake_bias` / `brake_bias_unit` | Ajuste `dcBrakeBias` leído por SDK, sin conversión de escala. Se compara como ajuste opaco del mismo auto; unidad vacía/desconocida no se transforma a porcentaje delantero. |
| `seconds_to_braking_start` | Estimación con velocidad actual constante hasta el extremo temprano del intervalo observado; solo regula anticipación/caducidad. |

Los nombres de curva provienen del mapa aproximado descrito en
[TRACK_MAP.md](TRACK_MAP.md). Los metros de frenada provienen de muestras y
longitud SDK, no de la geometría SVG. No hay datos de carteles físicos, entrada
exacta o ápice y no se deducen. Una frenada que comienza antes del cruce de meta
o no tiene liberación observada dentro de la vuelta se descarta.

## Compatibilidad y degradación

- Auto, pista, layout, longitud, fuente y sesión deben ser compatibles. Cambiar
  cualquier identidad o retroceder el reloj borra la referencia.
- Cambiar el valor o unidad de brake bias también borra la referencia y comienza
  otro segmento. La siguiente vuelta completa puede ser más lenta y aun así
  convertirse en referencia del nuevo ajuste. No hay regla lineal bias→pedal.
- Sin bias se puede revisar el pico histórico, pero no se emite objetivo
  numérico. `None` no significa cero. Si el canal no existe en ese auto, este
  incremento no ofrece anotación manual: se conserva el límite visible; antes de
  añadirla habría que registrar valor, escala, procedencia y vigencia por sesión.
- La comparación de velocidad permite una diferencia de hasta el mayor de
  5 km/h o 5%. Es un filtro heurístico conservador, no una tolerancia validada
  ni una predicción de distancia de frenado. Fuera de ese margen no se recomienda
  un porcentaje alternativo.
- La rampa debe estar acotada a 0,5 s y a un intervalo redondeado de hasta 50 m.
  Una rampa amplia conserva datos históricos sin consejo numérico.
- Freno ausente durante el episodio, falta de liberación, huecos >0,5 s,
  detención, replay, incidentes, daño explícito o boxes reportados impiden
  aprender ese episodio/vuelta. Una parada no se confunde con boxes confirmados.
- Las condiciones restantes no están verificadas: clima, neumáticos, combustible,
  resto del setup, daños no reportados y tráfico pueden cambiar. `confidence`
  permanece `limited`, `conditions_verified` y `target_validated` son `False`.
  El tráfico no demuestra error ni obliga a seguir la referencia. Tras un cambio
  conocido de condiciones se debe iniciar una nueva captura/sesión de la app.

## Voz y latencia

La voz usa una frase con ancla fija desde meta, no una cuenta regresiva que
envejece en una cola. El panel contiene el detalle de velocidad y bias para no
alargar la locución. El intento se realiza en una ventana estimada de 8–10 s;
caduca si no puede comenzar dentro de 0,5 s. Un backend ocupado no reintenta
la misma ocurrencia más tarde. La deduplicación usa la vuelta de destino para
evitar repetir una frenada anunciada antes y después de cruzar meta.

Navegación pausa a los 0,5 s sin muestras, aunque el indicador general conserva
su umbral de 2 s. Los cambios de contexto invalidan el aviso; ABS mantiene
prioridad. Las estimaciones no miden duración real de voz ni aceleración futura,
y no garantizan que el audio se haya escuchado.

## Evidencia reproducible

`python -X utf8 check.py` y `node --test tests/dashboard.test.cjs` ejercitan datos
sintéticos: rampa con velocidades distintas, velocidad comparable por progreso,
bias igual/distinto/ausente/sin unidad, cambio de identidad, referencia más lenta
tras cambiar bias, boxes en movimiento, cruces de meta, datos obsoletos, latencia
del backend, replay antiguo, render del panel y degradación sin números ficticios.

Ejemplo de fixture de curva 1: pico de Brake 0,8, intervalo 260–280 m desde meta;
produce «Curva uno. Referencia: pico 80 por ciento; inicio entre 260 y 280 metros
desde meta». Otra fixture con bias ausente produce «Brake bias no disponible;
referencia histórica sin objetivo numérico». Ninguna es una medición real ni
una recomendación para un auto o setup de un piloto.

Falta validar en una captura real autorizada la presencia/semántica de bias del
auto, ubicación de los intervalos, utilidad del margen de velocidad y audibilidad.
No se han iniciado sesiones ni modificado controles o configuraciones del juego.
