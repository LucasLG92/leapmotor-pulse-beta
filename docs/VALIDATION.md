# Validación del incremento de cargas manuales

Resumen del 13 de octubre de 2026. El contenido actual añade solicitudes de alta manual reservadas por propietario, intención/carga atómicas y reintentos explícitos sin duplicación. SQLite19/JSON15; el ledger operativo no forma parte de la exportación JSON. La compatibilidad sin `request_id` no ofrece deduplicación. [Contrato](API-CONTRACT.md), [recibo](CHARGE-SAVE-QUALIFICATION.json).

La fuente canónica se comprobó localmente:1242pruebas backend y126subcasos correctos, con34omisiones por SDK opcional ausente;51web/7móvil, tipado y builds web/móvil correctos, Svelte0errores/0avisos. La policy Android compilada con Java17/JUnit pasa8casos. Estas cifras son de validación local, no de CI ni de un vehículo. Los resultados del stage y las nuevas compilaciones nativas se registran después de congelar el contenido; no se anticipa su resultado aquí.

La QA del formulario real sustituye una respuesta por503después de guardar en SQLite propio. El reintento de la misma solicitud no aumenta filas ni coste. Campos vaciados, navegación/edición/descarte cancelados y fallo de reserva antes del alta conservan el intento o borrador según el caso. El fixture final contiene4cargas/28kWh/4,20EUR y4solicitudes enlazadas. La captura se observó en la herramienta, sin archivo de captura entregado. Revisión independiente sinP1/P2 confirmados; red móvil, lectores de pantalla, cargador, coche y clientes físicos siguen pendientes.

Los APK/IPA y recibos históricos conservan su versión y límites. No prueban esta modificación de policy/UI. Los clientes actuales siguen siendo candidatos genéricos0.1.1/build2 sin firma o aceptación física. No se cambia la configuración de una instalación, se despliega runtime ni se solicita acceso al coche o cuentas.

Se conservan nueve fases, siete puertas reales pendientes y A0–A12. Centinela/cámaras es la fase final. Recuperación futura: artefacto previo junto con su copia SQLite18 coherente; el código antiguo no puede abrir una SQLite19 migrada.

## Fase 5: diario manual privado, 13 de octubre

Suite final:1285backend/131subcasos/34skip del SDK opcional ausente,57web/7mobile; Svelte0errores0avisos, contratos/tipos y builds correctos.39pruebas nuevas de dominio/HTTP cubren aislamiento, CSRF, concurrencia/replay, revision, UTC/paradas/null, informe, export/restores y límites. La revisión corrige validación de enteros enormes, deriva no finita, revisión agotada, procesamiento fila a fila para informes/export y cantidades pequeñas conservadas por el editor. No acredita RSS con100000filas.

QA del componente real con SQLite/HTTP loopback propios: POSTcommit503/reintento mismo ID/fila, PUTcommit503/reintento revisión2, cambio de intención bloqueado, aviso al vaciar campos, cancelación de guardas, reserva fallida/recuperada, nulos y6e-7, reinicio con4filas, fallo de consulta/recuperación e informe anual con13meses. Formulario oscuro390px sin overflow, objetivos48px y consola limpia. Capturas observadas inline; no archivo guardado por falta de API admitida. Recepción de descarga y borrado UI no ejecutados; no se eluden permisos anteriores.

Android completo:77JUnit/0skip; Lint0errores17avisos, assemble/CRC/zipalign correctos.44assets móviles idénticos en APK y2bridgesvacíos; siete campos de conexión vacíos. APK genérico sin firma o instalación. Apple nuevo pendiente al congelar estas fuentes; el run histórico fase4 no cualifica este incremento. [Cualificación](MANUAL-JOURNEY-QUALIFICATION.json).
