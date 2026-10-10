# Avisos de terceros

El código y los activos originales de Pulse se publican bajo MIT. Las dependencias conservan sus licencias y avisos originales.

Dependencias de ejecución instaladas como paquetes independientes:

| Paquete | Versión fijada | Licencia | Fuente |
|---|---|---|---|
| FastAPI | 0.140.0 | MIT | https://github.com/fastapi/fastapi |
| Starlette | 1.7.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| Uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| Pydantic | 2.13.5 | MIT | https://github.com/pydantic/pydantic |

Los paquetes conservan sus LICENSE y metadatos en la distribución instalada; sus dependencias transitivas también conservan sus avisos. El paquete de fuentes entregado no contiene sus fuentes vendorizadas. Las licencias anteriores se verificaron en los metadatos del runtime utilizado para validar esta versión.

MATE-API es un extra opcional MIT fijado por commit en requirements-cloud.txt. Su [texto MIT y copyright originales](docs/licenses/mate-api-MIT.txt) se conservan junto con los avisos. Su instalación y tipos públicos se verificaron en un entorno separado, con fixtures sintéticas; no forma parte de la imagen base ni aporta material privado de conexión. Véanse docs/VEHICLE.md. No se ha probado una sesión ni un coche real.

Las marcas mencionadas pertenecen a sus titulares. Pulse no incorpora sus logotipos ni imágenes oficiales.

Los targets Capacitor Android/iOS 8.5.3 parten de plantillas MIT atribuidas con texto completo en `apps/mobile/LICENSES/Capacitor-MIT.txt`. Véase `apps/mobile/THIRD-PARTY-NOTICES.md` para Glance/WorkManager/AppAuth y el SDK Apple. La licencia propia no sustituye estas licencias de dependencias.

Dependencias de autenticación: Authlib 1.8.0 (BSD-3-Clause), HTTPX2 2.13.1 (BSD-3-Clause), joserfc 1.7.5 (BSD-3-Clause) y cryptography 50.0.2 (Apache-2.0 OR BSD-3-Clause); versiones y licencias contrastadas con metadata instalada. El runtime mantiene sus textos y avisos en dist-info. El inventario completo del entorno de validación está en docs/python-dependencies.json; el lock runtime excluye pytest y sus dependencias.

La PWA incorpora runtime Svelte y Workbox MIT. Los textos completos están en apps/web/THIRD-PARTY-NOTICES.md, incluido también en el contenedor. TypeScript/Vite/openapi-typescript y herramientas de compilación son dependencias de desarrollo con sus avisos originales; no se ejecutan en producción. SQLite se distribuye como dominio público según su fuente oficial: https://sqlite.org/copyright.html.

Paho MQTT 2.1.0 es el cliente de protocolo MQTT del puente optativo. La distribución fijada declara EPL-2.0 OR BSD-3-Clause; su LICENSE.txt permite EPL-2.0 o EDL-1.0. Pulse elige la opción permisiva EDL-1.0 (BSD-3-Clause), cuyo texto del tag v2.1.0 se reproduce abajo. No se incluye código Paho vendorizado. Fuente: https://raw.githubusercontent.com/eclipse-paho/paho.mqtt.python/v2.1.0/edl-v10

```text
Eclipse Distribution License - v 1.0

Copyright (c) 2007, Eclipse Foundation, Inc. and its licensors.

All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

  Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

  Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

  Neither the name of the Eclipse Foundation, Inc. nor the names of its
  contributors may be used to endorse or promote products derived from this
  software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

```

http-ece 1.2.1 cifra los mensajes Web Push con aes128gcm; MIT, Copyright (c) 2015 Martin Thomson. El paquete publicado se fija por SHA256, sin vendorizado. [Texto de licencia](docs/licenses/http-ece-MIT.txt); [mantenedor](https://github.com/web-push-libs/encrypted-content-encoding/blob/main/LICENSE); [publicación](https://pypi.org/project/http-ece/1.2.1/). Las primitives de cryptography50.0.2 y joserfc1.7.5 ya existentes se declaran ahora directamente para Fernet/P-256 y VAPID ES256.

Setuptools 84.0.0 se usa solo para construir el sdist verificado de http-ece. MIT, Python Packaging Authority; [texto de licencia](docs/licenses/setuptools-MIT.txt) extraído del wheel oficial cuyo SHA256 se fija en requirements-build-lock.txt. El prefijo de herramientas del builder no se copia a la imagen de ejecución. [Publicación oficial](https://pypi.org/project/setuptools/84.0.0/).

Leaflet 1.9.4 (BSD-2-Clause) proporciona la interacción del mapa, cargado bajo demanda. Texto íntegro en `docs/licenses/leaflet-BSD-2-Clause.txt` y avisos web/nativos. `@types/leaflet` 1.9.22 y su dependencia `@types/geojson` son MIT y solo desarrollo. El conjunto de datos OpenStreetMap es independiente del código; la interfaz conserva © OpenStreetMap contributors y enlace https://www.openstreetmap.org/copyright. Su ODbL no sustituye la licencia del código original.
