# Rendimiento local y transferencia de la PWA

## Experiencia B10: medición local del 11 de octubre de 2026

El build final conserva la carga diferida de las rutas y del mapa, fuentes del sistema y ninguna dependencia nueva. Las seis ilustraciones WebP originales suman 362850 bytes; se almacenan como activos genéricos de la PWA. El manifiesto de precache contiene 37 entradas y 999,10 KiB antes de negociación de transferencia; este tamaño incluye activos y entradas de precache, no equivale a una carga inicial del navegador.

En el servidor Uvicorn de laboratorio, 33 recursos únicos (incluido el service worker) entregan **1043661 bytes identity** o **573391 bytes con gzip negociado**. Las 24 respuestas comprimidas se descomprimen a los originales exactos; nueve recursos, incluidas las imágenes, permanecen identity. La medición cuenta cuerpos HTTP mediante sockets loopback, sin compresión automática del cliente. El conjunto completo supera el presupuesto histórico de 300000 bytes: las seis variantes visuales aportan un coste explícito. No se presenta la instalación completa como si cumpliera ese presupuesto ni se confunde con los bytes de la primera pantalla.

El chequeo de tipos final termina sin errores ni advertencias; 21 pruebas web y siete del puente móvil pasan. La revisión visual y de flujos usa exclusivamente datos ficticios en Chromium, seis rutas y los dos temas; comprueba 320, 390, 768 y 1280 px, formularios, confirmaciones, colores y actualización del service worker. El contraste computado de texto visible sobre fondos sólidos y alfa no registra fallos en las doce combinaciones finales, pero no sustituye una auditoría completa de accesibilidad ni VoiceOver/TalkBack.

Evidencia local: `../pulse-vehicle-experience-20261011/transfer-final.json`, logs de build y pruebas, capturas `review-confirm-*`, `contrast-confirm.json` y `breakpoints-final.json`. La medida no acredita transferencia HTTPS de producción, LCP/INP/CLS de campo, consumo, teléfonos físicos, coche o cargador. Los resultados históricos siguientes conservan su versión y alcance; A11 continúa parcial.


## Lector JSON nativo: comparación acotada del 10 de octubre de 2026

El lector comprueba ahora el tamaño acumulado antes de copiar cada bloque al
buffer. Mantiene el máximo de 64 KiB inclusive, los mismos errores, parseo,
destinos, permisos y cierre del stream. No añade dependencias, cambia SQLite,
modifica la UI ni requiere un APK nuevo.

Se compararon 48 muestras en las imágenes inmutables anterior y candidata:
24 por imagen, con procesos frescos, respuestas identity/gzip/gzip+deflate,
aceptación exactamente en el límite y rechazo, bloques de transporte de
127/65536 bytes y dos repeticiones. Los casos comprimidos rechazados representan
128 MiB lógicos generados incrementalmente. El decoder y el lector son reales;
el transporte es sintético, sin sockets, DNS, TLS, sesión o datos del operador.
Las 24 parejas conservan aceptación/rechazo, errores, bytes entregados y cierre.

Tracemalloc comienza después de imports y preparación, y mide asignaciones de
Python durante todo el flujo instrumentado, incluidos asyncio, cliente y
descompresor. El máximo observado pasa de 2490635 a 2485454 bytes. En las seis
parejas comprimidas que entregan un bloque decodificado de 1 MiB, la diferencia
es de 4961–5181 bytes. Los demás casos fluctúan decenas o centenares de bytes;
estas dos repeticiones no prueban una mejora relevante del pico total. El cambio
evita una copia innecesaria, pero el trabajo del decoder también contribuye al
pico. No se atribuyen diferencias de CPU/tiempo a rendimiento de producción:
ambas medidas incluyen el coste de tracemalloc.

Los contenedores de ensayo usan red desactivada, UID 10001, raíz de sólo lectura,
256 MiB, 0,5 CPU y tmpfs sin volúmenes persistentes ni puertos. Se retiraron los
recursos propios y se conservaron identidad, arranque, salud e inventarios de
Pulse. No acredita toda la memoria nativa, concurrencia, OAuth real, consumo
móvil ni rendimiento del servidor con integraciones activas. A11 permanece
pendiente de sus pruebas de campo.

## Reposo V35: laboratorio privado del 9 de octubre de 2026

La imagen V33 existente se mide sin reconstrucción, con sus 45 fuentes Python iguales a las actuales: Python 3.12.14, SQLite 3.53.4 y entrypoint real `python -m pulse`. Fixture original de 1000 cargas manuales, 1000 snapshots y 1000 eventos; demo e integraciones apagadas, tarifa manual. API limitada a 0,5 CPU y 256 MiB, red aislada, sin puertos publicados. Las sondas de readiness, resumen y totales del historial terminan antes de 30 segundos de estabilización. Durante los siguientes 120,001232 segundos no hay clientes, conexiones SSE, peticiones programadas ni healthchecks.

Se conservan 121 muestras de `/proc` del proceso API propio, una por segundo. UID10001, PID del namespace1 y tiempo de creación permanecen iguales en todas; intervalos efectivos de 0,998689–1,001069 segundos. RSS mínimo, mediano y máximo observado: **63762432 bytes (63,762432 MB decimales)**, por debajo del presupuesto existente de300000000 bytes. El muestreo no descarta picos entre muestras y excluye memoria de Docker, cliente, controlador, sistema y teléfono.

CPU se calcula como diferencia de `utime+stime`, dividida por `CLK_TCK=100` y por el tiempo monotónico: 24ticks, 0,24 segundos de CPU, **0,199998 % de un núcleo** o **0,399996 % de la cuota de0,5 CPU**. Resolución de los contadores:0,01 segundos. Es una medida descriptiva, sin introducir un nuevo umbral ni inferir consumo eléctrico o batería.

El principal recalcula resultados desde las muestras, verifica cuatro contenedores y un volumen propios retirados, Mate/22referencias de imagen/inventario conservados. Dos intentos previos fallaron antes de medir: el segundo identificó un error del auxiliar al abrir SQLite en un montaje readonly, mientras la API respondía200. La sonda corregida comprueba los mismos totales por GET privados con `limit=1`, fuera del intervalo, sin conceder escritura al cliente. Los intentos y scripts previos se conservan; ningún cambio del runtime ni del presupuesto.

Evidencia: `../pulse-idle-v35-qa/`, run `eff013562ac140c7b0475edba308fd14`, resultado bruto SHA256 `11ea86175fdda99d9fdaa3a4d3d3b2802e75f346d58ceb87224ae782094e2023`. Cualifica sólo reposo de esta API sintética con integraciones apagadas. A11 sigue parcial: falta medir sesiones SSE e integraciones activas, red/TLS real, p75/WebVitals y memoria/batería de móviles. No acredita coche, wallbox, widgets o CI Apple.

## Imagen actual V33: laboratorio privado del 9 de octubre de 2026

Imagen `sha256:3b586706f46b5f635c3cb3e148943e35f58750d36d9249ebbb997fb4846bf725`,Python3.12.14/SQLite3.53.4,45fuentesPython exactas al contexto V33. Los tres helpers de método son byteidénticos a V27; el controller cambia sólo imagen y conteo de fuentes. SQLite13/JSON11 del producto se reconocen sin cambiar carga, cuotas, escenarios, criterios o umbrales. No se reejecutó buscando un resultado favorable.

Fixture original:1000cargas manuales/1000snapshots/1000eventos observados, normal y demo. API0.5CPU/256MiB y cliente separado1CPU/128MiB, sin red exterior ni puertos publicados; cinco clientes,100respuestas por pasada,60pasadas y6000respuestas200/no-store. Proceso nuevo y reutilizado conservan sus definiciones V27; no se vacía la caché del sistema operativo. El p95 de cada pasada usa rango próximo, cronometra el cuerpo HTTP completo y valida después. La tabla muestra la mediana de cinco p95 por cohorte, no un percentil global.

| Escenario | API | Proceso nuevo, ms | Reutilizado, ms |
| --- | --- | ---: | ---: |
| Normal | Estado | 69.7030 | 71.4953 |
| Demo | Estado | 72.3486 | 72.1102 |
| Normal | Cambios, página20 | 64.7711 | 62.6842 |
| Demo | Cambios, página20 | 61.8478 | 66.8201 |
| Normal | Analítica energía | 271.9138 | 293.6308 |
| Demo | Analítica energía | 257.7421 | 305.0521 |

Las20pasadas de resumen tienen p95≤150ms; máximo92.8321ms. Sus cuatro medianas cumplen el presupuesto acordado. RSS pico73216000B en2874muestras bajo carga, por debajo de300000000B; no acredita memoria en reposo, batería o memoria del teléfono. Duración del ensayo273.786s. No se atribuyen diferencias respecto de V27 a optimizaciones nuevas: V33 no cambia el código de ejecución respecto de V32 y la analítica conserva su carga y tiempos variables.

El principal recalcula percentiles y medianas desde las6000muestras y verifica36procesos/76contenedores propios/1volumen, cuotas,45fuentes y cero consultas al guard de precios/fabricante. La salida complete sólo acredita ejecución; el cumplimiento150ms/300MB se verifica por separado. Cleanup retira todos los recursos propios y conserva Mate; el inventario antes/después es byteidéntico. Las21referencias de imagen previas conservan los IDs observados por image-ls y la nueva imagen se retiene. No CI externa, activación pública, OIDC/TLS, WebVitals/p75, INP, móviles, widgets o fabricante físico. Evidencia: `../pulse-installation-v33-qa/`, resultado bruto SHA256 `03c4043eb47a541b7e650b8b8ac5f765f8c421e86008412497fa1cedcbfb9148`. A11 completo sigue pendiente de sus pruebas de campo. Las medidas siguientes son históricas de sus versiones.

## Laboratorio histórico V22 del 9 de octubre de 2026

El backend medido es byteidéntico a V21. V22 corrige únicamente la primera carga del historial de Protección. Las medidas anteriores conservan sus fechas y cargas de trabajo; no se extrapolan a esta versión ni a dispositivos físicos.

### API y memoria actuales

Fixture propia SQLite10/JSON9:1000 cargas manuales dentro de30 días,1000 snapshots y1000 cambios observados, normal y demo. Cinco clientes concurrentes,100 respuestas por pasada, cinco procesos nuevos y cinco reutilizados por caso:60 pasadas/6000 respuestas200 con `no-store`. Se cronometra el cuerpo HTTP completo y se valida después. Percentil de rango próximo por pasada; la tabla usa la mediana de sus cinco p95. No se vació la caché del sistema operativo.

| Escenario | API | Proceso nuevo, ms | Reutilizado, ms |
|---|---|---:|---:|
| Normal | Estado | 6.5038 | 6.1734 |
| Demo | Estado | 4.8063 | 4.7400 |
| Normal | Cambios, página20 | 2.8609 | 2.6678 |
| Demo | Cambios, página20 | 2.9560 | 2.7747 |
| Normal | Analítica energía | 65.6497 | 65.2280 |
| Demo | Analítica energía | 66.7838 | 65.4846 |

RSS máximo72.888MB decimales, muestreado cada50ms en36 procesos propios. Estado queda por debajo del presupuesto150ms y RSS bajo300MB en este laboratorio. El RSS incluye arranque/trabajo; no es una medición aislada de reposo. Python3.12.14/SQLite3.53.1, Linuxx86_64, un Uvicorn en loopback. La imagen V21 cualificada utiliza SQLite3.53.4: este ensayo no mide esa imagen ni el servidor. Fabricante, evcc, MQTT y Push apagados; guard PVPC cero llamadas. Puerto9041 libre tras cierre. La analítica tiene una carga distinta a V13; la diferencia no demuestra por sí sola una regresión.

### Recursos finales y navegador

50 respuestas HTTP de25 recursos públicos del preview8792:600757B identity y193586B con negociación gzip,22 comprimidos y3 PNG identity. Cada respuesta expandida coincide con el build final. El conjunto completo comprimido permanece bajo300000B; incluye todas las rutas, mapa ySW. No incluye cabeceras, TLS ni cuerpos de API privados.

La carga inicial pública de Resumen observada sin caché usa34536B de JavaScript comprimido,6292B deCSS y41591B de cuerpos públicos con el HTML original. Límites conservados:150000/50000/300000B. El observador y el HTML instrumentado existen solo en QA y se excluyen del presupuesto del producto.

Chromium155 Linux, viewport solicitado440×900 y anchoCSS425 por scrollbar, escenario preparado sintético, sin limitar CPU/red. Se tomaron cinco recargas sin caché/SW y cinco con caché por cada una de seis rutas:60 muestras. Ambas series usan el mismo build; la caliente usa un origen loopback nuevo con fixture equivalente y precache propio. Sus recursos públicos registran transferencia cero desde red. Medianas de las observaciones LCP:

| Ruta | Sin caché, ms | Con caché, ms | Máximo CLS observado, ambas series |
|---|---:|---:|---:|
| Resumen | 52 | 48 | 0.01778 |
| Energía | 48 | 36 | 0.04019 |
| Navegación | 48 | 44 | 0.04516 |
| Actividad | 44 | 44 | 0.02142 |
| Protección | 44 | 40 | 0.01778 |
| Ajustes | 48 | 40 | 0.01778 |

Son snapshots tras observar el estado renderizado; no métricas finalizadas de toda la sesión, p75 de campo, móviles físicos o una certificación Core Web Vitals. No se midióINP. Los umbrales acordados siguen LCP2.5s/INP200ms/CLS0.1 y p75 móvil/escritorio; laboratorio y campo requieren evidencia distinta, conforme a [Web Vitals](https://web.dev/articles/vitals).

### Hallazgo corregido y límites

Antes del cambio, Protección registró CLS0.293–0.311: las tarjetas inferiores aparecían antes de las20 filas y se desplazaban al llegar el historial. Se dibuja inicialmente el estado cargando y las tarjetas se revelan al resolver la primera carga. El plazo de ocho segundos desde montaje no se reinicia por nuevas revisiones: cancela la solicitud vigente, invalida respuestas tardías y muestra reintento si vence. Los refrescos posteriores de un historial vacío mantienen las tarjetas disponibles. No cambia la página confirmada ni auth, red compartida, datos o dependencias.

QA verifica primera espera, plazo, reintento, vacío, error503, refresco vacío pendiente, demo y recuperación normal;320/440/1280 sin overflow. La revisión independiente cerró la ocultación indefinida del primer intento. No nueva emulación offline ni VoiceOver/TalkBack. Una muestra caliente conservaba el shellV21 sin observador y se excluyó; se usó el origen nuevo. Un comando CDP tardó395s pese a timeout: no se cuenta como latencia de Pulse. Opciones retiradas al cerrar la pestaña propia y viewport restaurado.

Evidencia desde la raíz del repositorio: `../pulse-performance-v22-qa/` contiene `api-results.json`, `browser-runs-final.json`, `browser-summary.json`, `browser-behaviors.json`, `transfer-final.json`, builds, firma y revisión. Preview8792 se conserva vivo sin reinicio; QA9041/9042/9043 cerrada. Sin nueva imagen, producción, activación pública o teléfonos. Rollback local: fuentes/buildV21, mismaDB10; sus fuentes/APK e imagen se conservan.

## Transferencia final V16

El 8 de octubre de 2026 se midieron por sockets las dos representaciones de los 25 recursos compilados, incluido `sw.js` y el código de mapas: 568784 bytes identity y 184904 bytes de cuerpo HTTP con gzip negociado, 22 respuestas comprimidas y tres PNG identity. Las 50 respuestas se contrastaron con los originales, descomprimiendo únicamente después de contar los bytes recibidos. Evidencia: `../pulse-navigation-v16-qa/transfer-final.json` desde la raíz del repositorio.

El conjunto completo permanece por debajo del presupuesto de 300000 bytes con gzip. Esta suma no mide cabeceras, TLS, Web Vitals, tiempo de renderizado, memoria del navegador ni instalación física. El service worker incluye el código genérico de Leaflet; importar el mapa bajo demanda evita inicializarlo antes de abrirlo, pero no elimina su código del precache. Las imágenes cartográficas y respuestas de navegación permanecen fuera del service worker. Las medidas API/RSS conservadas abajo pertenecen a V13 y no se repitieron.

## Compresión real V14

El preview local del 8 de octubre de 2026 entrega los 21 recursos únicos de la PWA, incluido `sw.js`, con 121860 bytes de cuerpo HTTP al aceptar gzip frente a 359221 bytes identity: reducción del 66,08 %. Se midieron ambas modalidades mediante sockets HTTP, sin descompresión automática del cliente. Las 18 respuestas comprimidas se descomprimen a los originales exactos; las tres imágenes PNG permanecen identity. Los originales y el precache son idénticos a V13. Evidencia: `../pulse-transfer-v14-qa/preview-transfer.json` desde la raíz del repositorio.

Los 121860 bytes son transferencia real del preview, distintos de los 120722 bytes gzip teóricos de la línea base V13. La medición suma cuerpos de recursos únicos, sin cabeceras, TLS, latencia de renderizado ni comportamiento de una instalación física. Confirma que el conjunto completo queda por debajo de 300000 bytes cuando se acepta gzip; identity mantiene 359221. La primera pantalla ya cumplía el presupuesto original de 300000 bytes. No se modificó Caddy ni se probó HTTPS. [Build y política de negociación](STATIC-ASSETS.md).

Las medidas de API/RSS siguientes pertenecen al laboratorio V13 y no se repitieron: V14 solo cambia la entrega de ficheros públicos. No se extrapolan a teléfonos ni a consultas al fabricante.

## Línea base V13 conservada

Medición del 8 de octubre de 2026, laboratorio Linux x86_64. No representa tiempos de un teléfono, Web Vitals, red exterior ni consultas al fabricante. Evidencia externa al paquete: `../pulse-ui-v13-qa/performance.json` y `performance.log` desde la raíz del repositorio.

## Transferencia del build final

Servidor real Uvicorn en loopback. Las 21 respuestas de activos coinciden byte a byte con los ficheros compilados. Se observó `Content-Encoding: identity`; los tamaños gzip siguientes son compresión teórica nivel 9 de cada recurso, no bytes transferidos comprimidos. No se midió Caddy.

| Navegación directa sin caché | Cuerpo total medido, bytes |
|---|---:|
| Resumen | 118519 |
| Energía | 169340 |
| Actividad | 110486 |
| Protección | 106387 |
| Ajustes | 189399 |

La primera pantalla Resumen equivale a 39229 bytes gzip teóricos. La PWA precarga todas las rutas: 25 entradas de manifiesto corresponden a 20 recursos únicos, con 339386 bytes medidos; incluyendo `sw.js`, 359221 bytes, o 120722 bytes gzip teóricos. Los recursos duplicados del manifiesto no se contaron dos veces en el total único. Este análisis suma cuerpos HTTP; no mide una instalación real, cabeceras, caché del navegador ni latencia de renderizado.

Resumen cumple el presupuesto de 300000 bytes en esta medición V13. La instalación completa supera ese umbral sin compresión; V14 verifica la reducción del cuerpo comprimido en el preview, descrita arriba. Falta verificar el despliegue. El límite Workbox de 300000 bytes por fichero no acredita un presupuesto total de instalación.

## API y memoria

Fixture SQLite separada con 1000 cargas manuales distribuidas en 30 días, primero sin demo y después con demo. Se midieron `/api/state` y `/api/analytics/energy` con cinco clientes HTTP concurrentes: cinco pasadas de proceso nuevo y cinco con el mismo proceso ya usado, 100 respuestas exitosas por pasada. Total: 40 pasadas y 4000 respuestas medidas. Sesiones de laboratorio solo en memoria. El caché del sistema operativo no se vació; «proceso nuevo» no significa disco frío.

| Estado | Endpoint | Mediana p95 proceso nuevo, ms | Mediana p95 reutilizado, ms |
|---|---|---:|---:|
| Solo manual | `/api/state` | 5.390 | 5.206 |
| Solo manual | `/api/analytics/energy` | 3.753 | 3.364 |
| Demo y manual | `/api/state` | 4.401 | 4.269 |
| Demo y manual | `/api/analytics/energy` | 3.784 | 3.230 |

Percentiles de cada pasada por rango más próximo; la tabla presenta la mediana de sus cinco p95, no un percentil global. El tiempo incluye recibir el cuerpo HTTP completo, sin validar el JSON dentro del cronómetro. RSS del proceso API muestreado cada 50ms: mediana 66480KiB, p95 67060KiB, pico 67092KiB. No mide memoria de la web ni del teléfono. El refresco final de activos conservó las 40 pasadas API; no se repitieron por un cambio de CSS.

Python 3.12.14, SQLite 3.53.1, FastAPI 0.140.0, Starlette 1.7.0 y Uvicorn 0.54.0, un proceso. Proveedor sin configurar; recolector, evcc, MQTT y Push apagados. El guard de precios registró cero llamadas externas. Abrir vistas sigue leyendo datos locales.

## Aceptación pendiente

Medir LCP/INP/CLS y p75 en uso real, memoria y consumo de Android/iPhone, transferencia HTTPS con Caddy, instalación completa y llamadas externas con integraciones cualificadas. Los resultados anteriores no cierran A11 ni los requisitos de dispositivo de `ACCEPTANCE.md`. No se introdujo instrumentación de laboratorio ni nuevas dependencias en la aplicación.

## Consultas a demanda V20

En un origen sintético nuevo, entrar en Energía sin demo y mantener plegados los registros produjo cero GET de `/api/charges` y `/api/analytics/energy`. Abrirlos realizó uno de cada; plegar y reabrir conservó esos contadores y una cotización de 7 kWh/1,05 EUR. Cada revisión visible vuelve a cargar los datos. Las consultas del cargador siguen independientes. Esto acredita reducción de consultas iniciales, no una nueva medida de latencia, memoria, batería o Web Vitals.


## V25: preparación de telemetría para analítica

La analítica conserva todos los snapshots seleccionados y sus validaciones.
Store reutiliza una sola preparación privada, acotada a los1000snapshots
máximos, cuando coinciden los nombres de columna, orden, valores y tipos
leídos en SQLite. La representación hexadecimal de los floats evita que la
igualdad numérica oculte sustituciones de representación. La entrada se
reemplaza completa solo tras validar todas las filas; un dato corrupto no
se convierte en una entrada válida.

Cada llamada mantiene su transacción/consulta, calcula el reloj y la ventana
y vuelve a agregar manual/demo y calcular cobertura. No se cachean respuestas,
autenticación, precios, datos de otro Store ni resultados de un rechazo. Un
cambio por escritura, importación o SQL externo invalida por datos leídos,
sin depender de revision, timestamp de la tabla o unTTL. El proceso siguiente
empezará con preparación vacía. No cambia SQLite10/JSON9, contratos ni UI.

El perfil anterior del mismo producto V22 en CT107,0.5CPU/256MiB, midió
1000 conversiones por consulta y tiempos de esa etapa93.5251ms normal y
88.4720ms demo en llamadas con wrappers; los tiempos incluyen sobrecoste y
esperas de la cuota. La serialización Pydantic mediana fue0.0244/0.0232ms.
Es evidencia para optimizar trabajo repetido, no una medida HTTP nueva.
El p95API final de este cambio se documenta por separado después de construir
y medir la imagen actual, sin inferir móviles/INP/p75 ni RAM idle.


V27 lee resumen y última telemetría en una transacción y un trabajo del
ejecutor. La regresión A+A / B+B demuestra la coherencia que fallaba con
escrituras intercaladas; el cuerpo del resumen conserva su semántica.
El perfil V26 midió esperas de cola relevantes con pocos milisegundos de
CPU por petición, sin explicar completamente la variación de
V25. No atribuir una mejora HTTP por reducir una llamada, ni usar el perfil
instrumentado de100respuestas como aceptaciónA11. Conservar carga/payload,
cuotas y presupuesto150ms; medir la imagen V27 exacta con el banco original
y declarar el resultado, incluso si falla. UI/bundles/APKV22 no cambian.
