# Fase 1: recuperación tras cortes de conexión

La revisión visual confirmada por Lucas en iPhone y Android se conserva. Las pruebas físicas de VoiceOver/TalkBack y recepción de archivos siguen pendientes; la fase 1 permanece abierta y no habilita empezar la fase 2. Photon continúa preparado sin instalar por elección de Lucas.

## Problema y corrección

Si el arranque perdía la conexión al leer el estado, Pulse mostraba el error y permitía Reintentar. Al volver automáticamente la red, reconstruía la pantalla y dejaba el foco en el cuerpo de la página aunque Reintentar estuviese enfocado. Se reprodujo en Chromium con el servidor loopback y datos sintéticos.

La recuperación automática reutiliza ahora el mismo flujo de recuperación de foco del reintento manual. Cuando se pierde el control enfocado, devuelve el foco al título visible. Si Reintentar no tenía el foco, no enfoca otro control. Las guardas existentes conservan el foco externo y respetan el foco del documento. No cambia API, autenticación, persistencia, backend ni clientes nativos.

## Comprobaciones ejecutadas

28 flujos E2E Chromium pasan, con dos escenarios nuevos. El primero corta realmente la red del contexto aislado, conserva el borrador de preferencias, muestra errores accesibles en español, libera los controles y mantiene el foco de exportación. Reconectar no repite el guardado: una acción explícita guarda el borrador y otra recibe un JSON válido. El segundo interrumpe la lectura inicial y comprueba la recuperación automática, con Reintentar enfocado o sin enfocar.

La sonda anterior registró foco BODY; la sonda corregida registra H1/page-title. Ambas comprueban el borrador, el guardado explícito y la descarga sin errores JavaScript. Svelte presenta cero errores y avisos, y el build pasa. El verificador de entrega mantiene sus 20 comprobaciones. Las suites generales y de dependencias se verifican además sobre los candidatos exactos en GitHub.

Una tentativa adicional de simular una pestaña inactiva no se consideró válida: Chromium sin interfaz seguía devolviendo foco del documento para la página original. Se retiró ese caso del ensayo; no acredita comportamiento de ventanas, Safari ni lectores reales. La guardia de foco existente sigue intacta.

## Entrega y límites

La entrega conserva 63 recursos web, los 62 archivos del backend, los cinco archivos Compose y los esquemas SQLite21/JSON17. El rollback conserva la imagen anterior con los datos actuales. Los recursos se contrastan por HTTPS estricto y los monitores de copias se comprueban después de activar la imagen.

Los ensayos usan SQLite temporal, servidor loopback y contexto sin service workers. El entorno excluye las variables PULSE operacionales. No contienen datos de Lucas ni consultan servicios externos. No exportan ni restauran producción, y no sustituyen la aceptación de PWA, archivos o accesibilidad en teléfonos reales. Las denegaciones históricas del navegador productivo conservan su alcance.

La continuidad observada durante una interrupción externa del servidor no acredita la recuperación integral del host, proxy, autenticación o un desastre de fase 8. Sus criterios originales permanecen pendientes.
