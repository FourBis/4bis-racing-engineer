# Cambios y reglas de integración

La rama principal es `develop`. Los cambios se preparan en ramas de trabajo y
se integran mediante pull request; no se realizan pushes directos a la principal.

Por decisión del propietario, solo se integran PR cuyo autor sea
**JeremiasBadilla**, verificado por el ID de cuenta GitHub **66529405**. Un repositorio
público permite que otras personas abran PR; el workflow de autor los cierra
automáticamente. Solo ese usuario tiene excepción a la restricción de actualización,
y exclusivamente mediante PR. Los administradores que pueden editar reglas siguen
siendo responsables de mantener esta configuración.

Las reglas remotas deben exigir `owner-pr` y `tests`, impedir borrar o forzar el
historial de `develop`, `main` y `master`, y resolver conversaciones antes de
integrar. `master`, si se incorpora posteriormente, recibe solo PR desde `develop`.

El control de autor usa `pull_request_target` sin checkout ni ejecución de código
del PR. Publica `owner-pr` exitoso sobre el SHA exacto si el autor está autorizado;
para otro autor cierra el PR sin sobrescribir estados de un SHA compartido. Los
tests normales usan token de lectura y datos sintéticos, sin SDK ni evidencia privada.

Antes de proponer cambios: `python check.py`. Nunca incluir fotos, videos,
telemetría real, informes de sesiones, credenciales o contexto de conversaciones.
