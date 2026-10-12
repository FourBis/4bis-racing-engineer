# Mapa aproximado de Road Atlanta

`corners.py` contiene fracciones aproximadas derivadas de rótulos del mapa de
Road Atlanta Full Course (TrackID 127). Las constantes están incluidas en el código;
el programa no descarga ni necesita archivos de mapa en ejecución.

Fuentes oficiales de iRacing:

- [Trazado](https://members-assets.iracing.com/public/track-maps/tracks_roadatlanta/127-roadatlanta-full/active.svg)
- [Meta](https://members-assets.iracing.com/public/track-maps/tracks_roadatlanta/127-roadatlanta-full/start-finish.svg)
- [Curvas](https://members-assets.iracing.com/public/track-maps/tracks_roadatlanta/127-roadatlanta-full/turns.svg)

Método: proyectar los anclajes de los rótulos sobre los dos bordes SVG, muestreando
12.000 posiciones por borde. Orientar cada recorrido por el orden de los rótulos,
normalizar respecto de meta y promediar ambas fracciones. Los rótulos no certifican
ápice, entrada o salida; la diferencia entre bordes no limita todo el error SDK.

Solo se asocian aproximaciones conservadoras a 1, 5, 6, 7 y 10a. Los puntos ambiguos
quedan sin identificar. Distancia y momento del aviso usan el inicio observado de
frenada y la longitud SDK, no metros del SVG. Las marcas manuales de revisión
vertical son independientes. Validar ubicación y anticipación conduciendo antes
de ampliar cobertura de los avisos de voz.

## Revisión después de conducir

`track_map.py` incluye 201 puntos del centro aproximado entre ambos bordes,
orientados desde meta, y los 13 rótulos del circuito. Son coordenadas esquemáticas
derivadas de las fuentes anteriores, no posiciones GPS ni la trazada del piloto.
No requiere descargas en ejecución.

`corner_review.py` divide la vuelta en ocho zonas amplias: preparación de curva 1,
curva 1, curvas 2–4, curva 5, curvas 6–7, recta y curvas 8–9, curvas 10a–10b y
curvas 11–12. Sus límites sirven para localizar diferencias; no son entradas,
ápices o salidas comprobados. No amplían las curvas habilitadas para voz.

La pérdida de cada zona es su tiempo local en B menos su tiempo local en A;
no repite la diferencia acumulada desde meta. Las métricas usan las muestras
originales. La reproducción visual conserva hasta 1.200 cuadros por vuelta e
interpola por tiempo transcurrido desde su propio cruce de meta. Cada auto lleva
su propio progreso y sus mandos; los canales desconocidos no se rellenan.
Al finalizar una vuelta, el marcador permanece en meta y los mandos dejan de
mostrarse. La comparación de trazas por distancia sigue en el detalle técnico.
