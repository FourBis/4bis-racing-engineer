# Flujo local implementado

```mermaid
flowchart TD
  subgraph Piloto
    Mode[Elegir demo, replay o live]
    Record[Iniciar o detener grabacion]
    Read[Leer trazas e informe]
  end
  subgraph App[4bis Racing Engineer local]
    Select{Fuente elegida}
    Demo[Demo sintetica determinista]
    Replay[SQLite abierto solo lectura]
    SDK[pyirsdk Windows solo lectura]
    Connected{SDK conectado y tick nuevo}
    Waiting[Mostrar desconectado u obsoleto y reintentar]
    Model[Sample validado con unidades y nulls]
    Persist{Grabacion activada}
    DB[(SQLite local)]
    Analysis[Observaciones de mandos con rangos de tiempo]
    Eligible{Vueltas completas y contexto comparable}
    Compare[Interpolar por progreso sin rellenar huecos]
    Limited[Conservar trazas y motivo de exclusion]
    Report[Dashboard loopback e informes JSON y Markdown]
  end
  Mode --> Select
  Select --> Demo & Replay & SDK
  SDK --> Connected
  Connected -->|Si| Model
  Connected -->|No| Waiting --> SDK
  Demo & Replay --> Model
  Record --> Persist
  Model --> Persist
  Persist -->|Si| DB
  Model --> Analysis --> Eligible
  Eligible -->|Si| Compare --> Report
  Eligible -->|No| Limited --> Report
  Report --> Read
```

`model.py` define el contrato común; `demo.py` produce muestras sintéticas;
`iracing.py` encapsula el ciclo read/close del SDK sin comandos al juego;
`storage.py` graba y reproduce; `analysis.py` delimita vueltas y hallazgos;
`app.py` coordina lectura y HTTP; `dashboard.html` dibuja las trazas.

El adaptador no depende del dashboard ni del grabador. Los mocks entran por el
constructor de `IRacingSource`; no se añadió una jerarquía de clases de una sola
implementación. Un lock de la app serializa SQLite y muestras; si se requiere
captura de alta frecuencia, el siguiente incremento debe medir latencia y separar
la escritura por lotes, conservando errores y cierre seguro.
