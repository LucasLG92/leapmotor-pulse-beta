# Repositorios de referencia

Catálogo de referencias públicas para diseñar Leapmotor Pulse como aplicación original. Las URLs llevan a los proyectos fuente; los SHA fijan la revisión observada el 7 de octubre de 2026. No se descargó ni incorporó código, claves, certificados ni activos.

## Referencias Leapmotor

| Proyecto y revisión observada | Licencia publicada | Ideas que merece evaluar | Uso de código en Pulse |
|---|---|---|---|
| [LPB10](https://github.com/txurtxil/LPB10), `main` en [`b254e13`](https://github.com/txurtxil/LPB10/commit/b254e13dd074cebc3828e9b8389b78e6320913eb) | GNU GPL-3.0 ([LICENSE](https://github.com/txurtxil/LPB10/blob/main/LICENSE); SPDX de GitHub: `GPL-3.0`) | Referencia funcional de una app Flutter para Android: estado, controles, historial local reconstruido a partir de muestras, alertas, exportación/restauración y diagnóstico de estado crudo. El propio README distingue señales confirmadas por modelo de señales aún no verificadas; describe B10 probado en coche y otros modelos con cobertura parcial. | **Solo ideas y comportamiento observable. No reutilizar código GPL** en la aplicación original con licencia propia. El proyecto indica que el usuario aporta su certificado; no distribuye ese material. Los detalles funcionales publicados son afirmaciones de sus autores, no validación de Pulse. |
| [MATE-API](https://github.com/ProtossBlaster/MATE-API), commit [`5b7d30c`](https://github.com/ProtossBlaster/MATE-API/commit/5b7d30c30ae10eb18677214e36a9d42398a769e6) | MIT (LICENSE del repositorio; SPDX de GitHub: `MIT`) | Referencia documental sobre separación entre autenticación, sesión, transporte, lecturas y comandos; capacidad/permiso; telemetría como evidencia distinta de respuesta HTTP. Su README dice que la integración necesita material de aplicación, identidad de instalación, parámetros privados y gestión de certificado de cuenta. | La licencia MIT permite reutilización bajo sus condiciones, pero **esta versión de Pulse no incorpora MATE-API; puede añadirse como dependencia permisiva en una integración futura, conservando sus avisos**. No contiene por sí solo los materiales privados necesarios para conectarse. |
| [leapmotor-ha](https://github.com/kerniger/leapmotor-ha), `main` en [`4d49d5e`](https://github.com/kerniger/leapmotor-ha/commit/4d49d5ef7cd1cf0eca53a3120e42b33df0070575) | MIT (LICENSE; SPDX de GitHub: `MIT`) | Referencia para modelar una integración de Home Assistant, credenciales/material de cliente proporcionados fuera del código, estados de conexión y límites de compatibilidad por modelo. Su README indica que los certificados de aplicación no están en el repositorio. | **Permite reutilización de código MIT** conservando aviso y licencia; Pulse no la necesita en esta fase. Puede servir como fuente pública de requisitos y casos por modelo. |
| [leapmotor-api](https://github.com/markoceri/leapmotor-api), `main` en [`9e71ab0`](https://github.com/markoceri/leapmotor-api/commit/9e71ab0fffb2e07948c650db2a9613b0e712a56a) | GNU AGPL-3.0 (LICENSE; SPDX de GitHub: `AGPL-3.0`) | Referencia para un cliente Python independiente de Home Assistant y la distinción entre cliente de API y aplicación consumidora. Su README atribuye la investigación de protocolo a `leapmotor-ha` y requiere certificado y clave de aplicación. | **No reutilizar código AGPL** para la licencia propia escogida. Consultar únicamente documentación pública; el proyecto remite a un repositorio separado para certificados, que no se incluye ni se inspecciona aquí. |

## Referencia de producto EV autohospedado

| Proyecto y revisión observada | Licencia publicada | Ideas que merece evaluar | Uso de código en Pulse |
|---|---|---|---|
| [TeslaMate](https://github.com/teslamate-org/teslamate), `main` en [`96119f6`](https://github.com/teslamate-org/teslamate/commit/96119f65a98ecc2f6d3584c07af4d0b952326daa5) | GNU AGPL-3.0 (LICENSE; SPDX de GitHub: `AGPL-3.0`) | Límites explícitos de integración, documentación de operación, publicación de imágenes multiplataforma y MQTT como contrato de integración documentado. Es un registrador Tesla, así que su producto y API no validan el comportamiento de Leapmotor. | **No reutilizar código AGPL** en Pulse. Referencia de arquitectura/documentación únicamente. |

## Discrepancia de versión en MATE-API

El commit indicado existe: su [`pyproject.toml`](https://github.com/ProtossBlaster/MATE-API/blob/5b7d30c30ae10eb18677214e36a9d42398a769e6/pyproject.toml) declara `mate-api` versión `0.1.0a16`. Sin embargo, el README de `main` observado durante esta revisión aún declara `0.1.0a9`, y la referencia de Git `v0.1.0a16` devolvió HTTP 404. Por eso el catálogo fija el commit y la versión que declara ese archivo, pero **no presenta `0.1.0a16` como tag o release publicado**.

La licencia del cliente no concede derechos sobre certificados, claves, perfiles privados, credenciales, marcas o activos de Leapmotor. Ningún certificado o clave de los repositorios referenciados se descargó ni debe añadirse a Pulse.

## Referencia de contrato ABRP, 9 de octubre de 2026

[iternio/ovms-link](https://github.com/iternio/ovms-link), revisión [`d3eadc7`](https://github.com/iternio/ovms-link/commit/d3eadc79223760772b34ba50fa54add962ec3d60), es una referencia oficial de telemetría v1. Se contrastaron método, envelope, unidades y confirmación del servidor; no se reutilizaron código, tests, activos ni algoritmos. Su licencia no se verificó y no se presume permiso de incorporación. [ABRP.md](ABRP.md) describe el contrato original y sus límites. La página Postman enlazada por ABRP agotó tiempo; el endpointalpha de Planning v2 no se adoptó como sustituto.
## evcc: contrato temporal para cargas automáticas

Se conserva [evcc-io/evcc](https://github.com/evcc-io/evcc) como referencia primaria de interoperabilidad, fijada en esta investigación a `7db12597018f97076633cdd8262d434a89893fd2` (0.316.2). Solo se contrastaron interfaces, campos, unidades y límites; no se incorporan código, algoritmos, tests ni activos del proyecto. El nuevo documento [CHARGER-INTERVALS](CHARGER-INTERVALS.md) distingue energía por buckets y sesiones, cobertura desconocida y atribución pendiente. Una referencia guardada no concede permiso para reutilizar su implementación o su licencia.

## Centinela y cámaras: contraste del 12 de octubre de 2026

Se consultaron metadatos y documentación públicos de [Mate, a28901d](https://github.com/ProtossBlaster/leapmotor-mate/blob/a28901d4cf16703ecb02096398ba25088100331c/README.md), [LPB10, 38cd101](https://github.com/txurtxil/LPB10/blob/38cd101f8790e9c71085557e9d0ad3b03221b6fe/README.md) y el SDK ya fijado en5b7d30c. AGPL/GPL se mantienen como referencias funcionales, sin incorporar código, algoritmos, tests o activos. Los cuatro archivos públicos del SDK contrastados coinciden porSHA256 con la caché; no se ejecutó el cliente ni se cambió su pin.

Mate describe un control centinela condicionado por cuenta; LPB10 distingue vigilancia desde la app y ausencia de cambio observado tras aceptar220 en su ensayo. El SDK separa220/right220 de400 deshabilitado y nombra3636 como enumeración cruda. Estas observaciones no acreditan soporte o actuación en el vehículo objetivo, ni stream de vídeo.

El manual oficial B10 neerlandés describe dashcam/USB a bordo; la ficha europea describe360 de aparcamiento. Se conserva la variante de mercado de las fuentes y no se extrapola disponibilidad remota a la unidad española. La [matriz y secuencia de fase9](SENTINEL-CAMERAS.md) enlaza fuentes originales, límites y cualificación pendiente. No se descargaron binarios de fabricante, se accedió a material privado, se instaló Mate/LPB10 ni se efectuó una nueva entrega.
