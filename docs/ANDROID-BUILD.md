# Build Android

## Entrega vigente, 15 de octubre de 2026

Piloto0.1.1/código2 entregado con la firma anterior y la interfaz actual.47assets+2stubs exactos,104JUnit/9bridge,Lint0errores17avisos. Lucas confirma actualización/preferencias, retorno OAuth, widget tamaños/tema/texto/TalkBack y apertura; también red/reintento, logout/relogin y desactivar copia/desvincular tras reabrir/refrescar. Caducidad real, refresco bloqueado/autónomo y demás aceptación siguen pendientes. Es firma debug piloto; APK/configuración/clave quedan fuera de la beta. [Recibo y límites](PHASE2-DELIVERY-20261014.json). Las secciones inferiores son históricas.

## Registro anterior, conservado como histórico

El candidato local está identificado como versión **0.1.1**, `versionCode 2`. La preparación actual completó el ensamblado genérico de release **sin firma**. No se incluye ni distribuye el APK resultante, y no se ha probado una instalación física con este candidato.

Para reproducir el build, usa JDK, SDK Android y dependencias fijadas por el repositorio, instala las dependencias con lockfile y ejecuta el target Android definido en `apps/mobile/android`. Revisa el resultado localmente antes de firmar o distribuir.

La configuración de una instalación concreta —origen, OAuth, issuer, audiencia, callback y claves de firma— debe suministrarse fuera del repositorio. No guardes certificados, secretos, APK configurados ni hashes de firma en la documentación o en el control de versiones.

El ensamblado y los tests JVM no prueban OAuth real, asociación HTTPS, actualización, widget, permisos, consumo o compatibilidad de teléfono. Estos criterios continúan en [VALIDATION.md](VALIDATION.md) y [DELIVERY-STATUS.json](DELIVERY-STATUS.json).

