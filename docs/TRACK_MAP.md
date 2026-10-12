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
de ampliar cobertura o usar las posiciones como límites de análisis por curva.
