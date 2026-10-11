# Comprobación de dependencias

Pulse sigue en beta. El workflow `dependency-security` consulta avisos conocidos
con [OSV-Scanner](https://google.github.io/osv-scanner/) 2.6.0, fijado mediante
SHA-256. Se ejecuta en pull requests, cambios de locks/checker y de forma manual.
Es independiente de los builds; no instala paquetes ni despliega Pulse.

Comprueba seis archivos con las versiones realmente fijadas:

- `requirements-lock.txt`: backend y transitivas.
- `requirements-dev-lock.txt`: entorno de desarrollo Python.
- `requirements-build-lock.txt`: herramienta de compilación Python.
- `apps/web/pnpm-lock.yaml`: herramientas y dependencias web.
- `apps/mobile/pnpm-lock.yaml`: herramientas y dependencias móviles.
- `apps/mobile/android/app/gradle.lockfile`: dependencias Android resueltas.

Las entradas repetidas entre entornos se comprueban en sus respectivos archivos;
el conteo de entradas no equivale a un conteo de paquetes únicos. El checker exige
un inventario no vacío para cada archivo. Falla ante un aviso conocido o un error
de descarga, integridad, consulta o parsing. No filtra por severidad ni incorpora
excepciones. Una consulta sin avisos no garantiza ausencia de vulnerabilidades.

Para reproducirlo en Linux x86_64 con Bash, curl, sha256sum y Python 3:

```sh
bash scripts/check-dependencies.sh
```

Solo usa los locks indicados y una configuración vacía de OSV. No analiza el
directorio del servidor, almacenes de secretos o configuraciones personales.
La consulta remite identificadores/versiones de dependencias públicas a OSV.
No ejecuta código de los paquetes, resolución de rangos o call analysis.

## Corrección de la herramienta iOS

`xcode@3.0.1`, dependencia de desarrollo de Capacitor CLI, incorporaba `uuid@7.0.3`,
incluido en [GHSA-w5hq-g745-h8pq](https://github.com/uuidjs/uuid/security/advisories/GHSA-w5hq-g745-h8pq).
El override se limita a `xcode@3.0.1>uuid` y fija `11.1.1`, versión corregida que
mantiene CommonJS. El consumidor usa `uuid.v4()` sin buffers; no se ha demostrado
explotación en Pulse. La corrección elimina la versión afectada del lock, sin
añadir una excepción al scanner ni cambiar las dependencias de la aplicación.

## Pendiente de comprobación

Este check no cubre sistema operativo, imagen y bibliotecas nativas embebidas,
toolchains/SDK, Swift Package Manager, el SDK B10 opcional fijado a Git ni errores
propios de Pulse. Tampoco prueba permisos, aislamiento, autenticación, dispositivo
físico o servicios reales. Estas revisiones y las pruebas completas de seguridad
y recuperación conservan sus criterios de aceptación separados.

## Resolución Swift de iOS

El job manual `verify-ios` conserva el `Package.resolved` generado por Xcode
y un inventario de la revisión efectivamente resuelta. La comprobación exige
la dependencia externa declarada actualmente, `capacitor-swift-pm` 8.5.3,
su URL pública oficial y un commit completo coincidente con el checkout
utilizado. Verifica el hash del manifiesto oficial de esa versión y vuelve a
comprobar resolución, revisión y manifiesto después de los builds Debug/Release.
Si faltan archivos, hay pins inesperados o cambia la resolución, el job falla.

`scripts/ios-spm-audit.py` consulta la API oficial OSV por esa revisión,
sin credenciales y con TLS, destino fijo y límites de tiempo/tamaño. Falla
ante avisos, errores o una respuesta incompleta. No añade dependencias de
ejecución ni cambia el checker de seis locks. Una respuesta vacía significa
que OSV no devuelve avisos conocidos para ese commit en esa consulta;
no demuestra ausencia de vulnerabilidades ni cobertura completa del SDK.

El paquete Swift incluye dos binary targets, Capacitor y Cordova, con sus
checksums declarados. La integridad de la descarga gestionada por SwiftPM
y la revisión del wrapper no certifican la seguridad del código binario.
El artefacto `ios-spm-security`, retenido siete días, contiene únicamente
el lock validado, inventario, consulta resumida y recibo; no configuración
del operador, claves, credenciales ni datos del vehículo. Su presencia debe
comprobarse en un job real antes de afirmar que se obtuvo esta evidencia.

## Imagen de ejecución

La comprobación de locks no cubre todos los componentes de la imagen. Una
auditoría adicional con OSV-Scanner 2.6.0 sobre un archivo de `docker image save`
revisa el filesystem final, sin ejecutar la imagen ni exportar contenedores,
volúmenes o configuración del operador. Se incluyen todos los avisos, también
los clasificados como `unimportant` o `ignored`; los IDs y las entradas de
paquetes no equivalen a escenarios de explotación independientes.

El runtime conserva Python 3.12.14 y pasa a la imagen oficial slim Trixie,
fijada por digest. Los builders y la SQLite propia permanecen en sus etapas
anteriores: no se copian paquetes Trixie dentro de una base Bookworm. El
Dockerfile actualiza `libssl3t64`, `openssl` y `openssl-provider-legacy` a
`3.5.7-1~deb13u3` desde el snapshot Debian firmado `20261010T000000Z`.
Es la revisión de Debian con sus parches, distinta de la release upstream 3.5.9.
El [tracker de Debian](https://security-tracker.debian.org/tracker/source-package/openssl)
marca corregidos los seis avisos que seguían abiertos en OpenSSL 3.0.22 de
Bookworm. OpenSSL 3.5 tiene soporte LTS hasta abril de 2030 según su
[política de soporte](https://openssl-library.org/policies/releasestrat/).
Los snapshots requieren futuras actualizaciones deliberadas; no se actualizan
solos ni garantizan que nunca aparezcan nuevos avisos.

El candidato amd64 conserva las 49 fuentes Python y 47 recursos web de su
baseline. En pruebas aisladas se comprobaron arranque, esquema 14 y 24 tablas,
TLS 1.3 y rechazo de hostname incorrecto; 251 pruebas existentes pasaron y una
prueba del SDK B10 opcional se omitió porque ese SDK no está instalado. También
se comprobó TLS del shell público y del descubrimiento OIDC, sin sesión o
credenciales. Esta evidencia no sustituye login humano, avisos físicos,
servicios del coche ni cualificación de otras arquitecturas.

El análisis de la imagen pasa de 132 a 70 IDs registrados: 63 se retiran y uno
se añade. Cambian distribución e inventario, por lo que no se presenta como
63 vulnerabilidades reparadas. Se mantienen los 70 avisos y la salida 1 del
scanner, sin filtros ni excepciones; su alcance y la seguridad integral siguen
pendientes de revisión.

El ID añadido, CVE-2026-39113 de SQLite Debian, muestra una discrepancia:
OSV contiene un rango abierto para Debian 13, mientras que el
[tracker primario](https://security-tracker.debian.org/tracker/CVE-2026-39113)
marca el paquete no afectado/corregido e indica que el código vulnerable no
apareció en una versión Debian publicada. La SQLite activa conserva su
binario original 3.53.4; no expone las funciones SQLar ni el símbolo de esa
extensión en la sonda ejecutada. El aviso permanece visible: esa comprobación
acotada no elimina todos los avisos de SQLite ni acredita parches por el número
de versión.

Después de `pip check`, la etapa final retira `pip` y `ensurepip`. Los
instaladores siguen en build; los extras opcionales deben incorporarse allí.
Esto reduce la superficie disponible en ejecución, sin eliminar los bytes de
capas base ni prometer una descarga menor. No se eliminan bibliotecas del
sistema para ocultar resultados del scanner.

`scripts/build-container.sh` comprueba UID 10001, Trixie, las tres revisiones
exactas de OpenSSL y PCRE2, ausencia de Node/setuptools/pip/ensurepip, imports y gzip.
Comprueba por separado OpenSSL 3.5.7 del módulo `ssl` y OpenSSL 4.0.3 embebido en
cryptography 50.0.2: son bibliotecas distintas.

SQLite activo sigue compilado desde la fuente oficial 3.53.4 y cargado desde
`/usr/local/lib`; no incluye Session ni FTS5 en esta compilación. El inventario
Debian contiene otra SQLite 3.46.1, cuyos avisos no se descartan automáticamente.
En particular, ver 3.53.4 por sí solo no prueba el parche adicional de
[CVE-2026-50813](https://security-tracker.debian.org/tracker/CVE-2026-50813).

Un build correcto o una sonda de versión no sustituyen la auditoría de rutas
alcanzables, bibliotecas embebidas, compatibilidad, licencias y seguridad
integral. Pulse continúa en beta.

## Parche PCRE2 y alcance de la revisión de codecs

El runtime exige `libpcre2-8-0=10.46-1~deb13u3` desde el mismo snapshot
firmado. El [tracker de Debian](https://security-tracker.debian.org/tracker/CVE-2026-103111)
identifica esta revisión como corregida para CVE-2026-103111. Mantiene la base
Trixie y no añade dependencias, excepciones al scanner ni cambios de aplicación.
El checker del build comprueba la revisión instalada; la cifra de 70 IDs de la
sección anterior corresponde al baseline previo a este parche.

La revisión de las 49 fuentes Python identifica uso directo de zlib para
validar PNG de navegación y descompresión HTTP transitiva en navegación, PVPC,
evcc e introspección OIDC. Las lecturas acotan los bytes decodificados;
`Content-Length` no acredita una cota sobre su expansión. Los usos directos
de expresiones regulares emplean Python `re` y las validaciones de Pydantic.
Esto no demuestra ausencia de todas las llamadas transitivas a PCRE2.

El aviso de bzip2 CVE-2026-42250 se refiere al ejecutable `bzip2recover`;
no se descarta por la presencia de una biblioteca ni por un mapa de proceso.
El aviso zlib CVE-2026-85091 presenta una discrepancia entre el tracker y el
[BTS de Debian](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1146895).
Esos avisos y CVE-2026-27171 se mantienen visibles mientras no haya evidencia
suficiente. Mapas de bibliotecas y roundtrips benignos no acreditan todas las
rutas del programa ni equivalen a pruebas de explotación o seguridad integral.

El candidato amd64 con este parche pasa las 251 pruebas de runtime existentes
y conserva una omisión por el SDK opcional ausente. Mantiene las 49 fuentes
Python, 47 recursos web, binario SQLite, configuración de imagen y restricciones
comprobadas del baseline. La sonda de un consumidor PCRE2 (`grep -P`) pasa;
no es una prueba de explotación. El inventario de paquetes del scan cambia
únicamente pcre2 de u2 a u3.

El nuevo scan de la imagen registra 69 IDs, uno menos que el baseline de 70:
se retira CVE-2026-103111 y no se añaden IDs. Los 69 avisos restantes siguen
visibles, sin excepciones y con salida 1 del scanner. La seguridad integral
continúa pendiente. Estos resultados no acreditan otras arquitecturas,
servicios reales, login humano ni dispositivos físicos.

## Regresión de respuestas HTTP comprimidas

`tests/test_upstream_compression.py` ejercita los lectores de PVPC, evcc,
lugares de navegación, cuerpo HTTP de tiles e introspección nativa. Usa
`AsyncByteStream` y `MockTransport` del cliente fijado `httpx2==2.13.1`, sin
conexiones de red, cuentas ni credenciales. El cliente realiza la
descompresión; las pruebas no sustituyen el decoder por una implementación
simulada.

Los 80 casos comprueban respuestas sin compresión, gzip, deflate, deflate crudo
y la cadena `gzip, deflate`, con tamaños decodificados un byte por debajo,
exactamente en el límite y un byte por encima. Otros casos declaran el pequeño
tamaño comprimido mientras el cuerpo expandido supera el límite. Se verifica
aceptación o rechazo según el límite de cada lector y cierre del stream,
también al rechazar la respuesta. Los límites actuales son 512 KiB para PVPC,
1 MiB para evcc, 256 KiB para lugares, 128 KiB para tiles y 64 KiB para la
respuesta nativa.

Los 80 casos pasan localmente; el conjunto con las pruebas existentes de esos
lectores suma 271 casos correctos, incluyendo los nuevos. Esta evidencia cubre
el control del cuerpo HTTP: no acredita PNG válidos, OAuth completo, límites de
memoria/RSS, todos los codecs opcionales, servicios reales ni ausencia de CVE.
No requiere cambiar el código de ejecución, dependencias o configuración.

## Corrección de componentes Rust de Pydantic

La pareja oficial `pydantic==2.14.0` / `pydantic-core==2.50.0` sustituye
`2.13.5` / `2.46.5`. El manifiesto de Pydantic requiere esa versión exacta
de Core. Se conservan las demás versiones y hashes de los locks de runtime
y desarrollo; los artefactos nuevos tienen hashes SHA-256 de PyPI.

El Cargo.lock publicado y el SBOM del wheel CPython 3.12/Linux de Core
coinciden en las versiones registry y declaran PyO3 `0.29.2` y LRU `0.18.5`.
Superan las versiones corregidas de
[RUSTSEC-2026-0176](https://rustsec.org/advisories/RUSTSEC-2026-0176.html),
[RUSTSEC-2026-0177](https://rustsec.org/advisories/RUSTSEC-2026-0177.html) y
[RUSTSEC-2026-0253](https://rustsec.org/advisories/RUSTSEC-2026-0253.html).
Los dos primeros avisos tienen alias GHSA; cinco identificadores describen
tres condiciones. No se ha demostrado explotación en Pulse.

La consulta OSV 2.6.0 del candidato comprueba 754 entradas en los seis locks,
100 paquetes en Cargo.lock y 99 componentes en el SBOM: ocho inventarios
completos, salida 0 y ningún aviso conocido, sin exclusiones. El SBOM declara
`all_targets`; no acredita por sí solo todos los componentes compilados o
alcanzables. El checker habitual de seis locks no inspecciona estos dos
inventarios Rust; la comprobación nativa fue adicional y debe repetirse al
actualizar esos artefactos.

La cualificación local usa Python 3.12.14 y SQLite 3.53.4: 977 pruebas Python
correctas, seis del SDK opcional omitidas, contrato OpenAPI existente exacto,
tipos, pruebas y builds web/móvil correctos. Esta evidencia no acredita que
una instalación particular esté actualizada ni pruebas físicas. Los avisos
de la imagen del sistema, otras dependencias nativas y la seguridad integral
mantienen su evaluación separada.
