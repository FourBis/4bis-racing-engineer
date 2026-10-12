# 4bis Racing Engineer

- Responder en español de Chile. Cambios pequeños, stdlib y patrones existentes.
- Leer README.md, docs/ARCHITECTURE.md, docs/SDK_CHANNELS.md y docs/TRACK_MAP.md.
- El SDK es solo lectura. No modificar controles, calibración, setups ni archivos
  del juego. No automatizar conducción.
- No publicar fotos, videos, telemetría, informes privados, credenciales o contexto
  de conversaciones. Los tests usan datos sintéticos y directorios temporales.
- No sustituir canales ausentes por cero ni confundir mando normalizado de freno
  con fuerza física. No diagnosticar suspensión, daño o bloqueo sin evidencia.
- Distinguir tests sintéticos, conexión SDK real y validación conduciendo. Una
  vuelta rápida observada no demuestra un objetivo óptimo ni ausencia de tráfico.
- El piloto adapta su ejecución al tráfico; apartarse de la guía no prueba un error.
- Ejecutar `python check.py` para cambios de lógica y añadir una regresión acotada.
- Trabajar en ramas `codex/` desde `develop` remota actualizada y limpia. Obtener
  remotos/worktrees y comparar referencias antes de editar. Si falta `develop`,
  hay cambios pendientes/divergencia o el remoto no se verifica, detenerse y avisar.
- Integrar mediante PR hacia `develop`. `master` solo mediante PR desde `develop`.
  No crear remotos, fusionar ni publicar sin autorización del usuario.
