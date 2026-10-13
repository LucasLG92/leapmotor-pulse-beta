# Pulse: referencias para una experiencia cotidiana

Investigación del13oct2026 para cuidar exclusivamente UI/UX. Las referencias sirven para analizar jerarquía, interacción y tono; no se incorporan código, recursos gráficos, marcas ni funcionalidades de esas aplicaciones.

| Referencia primaria | Evidencia consultada | Aplicación propia en Pulse |
|---|---|---|
| [Rivian Mobile App3.0](https://rivian.com/stories/app-software-3-0) | Artículo oficial y una imagen oficial de la app inspeccionada en navegador. Vehículo destacado, controles circulares y grupos visuales distintos. | Dar más espacio al B10 original, asociar autonomía/batería y distinguir los accesos por su función. Conservar procedencia y bloqueo de controles físicos. |
| [Tesla ChargeStats](https://www.tesla.com/en_ph/support/tesla-app/charge-stats) | Documentación oficial y gráfico de la app inspeccionado en navegador. Energía/coste lideran y el desglose conserva unidades y localización. | Jerarquía de las cifras y detalle progresivo en cargas y comparativas existentes. No adoptar cálculos de ahorro o datos que Pulse no tenga. |
| [Tibber: costes y precios](https://support.tibber.com/hc/en-us/articles/48270714748305-Energy-costs-and-electricity-prices-in-the-app) | Texto de soporte oficial: exploración de intervalos y costes estimados. No se probó la app. | Separar dato y explicación; conservar supuestos y aproximaciones junto a los resultados. |
| [Oura: nueva experiencia de la app](https://ouraring.com/blog/new-oura-app-experience/) | Artículo oficial del3oct2024 sobre Today, Vitals y MyHealth. Se usa como referencia histórica de diseño, no como descripción exhaustiva de la app actual. | Mostrar primero lo cotidiano y abrir los detalles a petición. Tono personal y vacíos que explican un siguiente paso existente. |
| [Apple Wallet: jerarquía de campos](https://developer.apple.com/design/human-interface-guidelines/wallet/) | Guía oficial: campo principal, secundarios y detalles. | Un dato protagonista por grupo y menos elementos compitiendo por atención. No se integra Wallet. |
| [Tobi en X](https://x.com/tobiadonadon_/status/2106430529113301163) | Referencia del usuario ya leída y documentada en VISUAL-DIRECTION. | Mantener una tesis visual propia, comparar alternativas y revisar el render. |

La dirección de la iteración anterior fue **tu garaje personal, con calma y respuesta**. Se conserva la identidad Claro/Charcoal y el sistema de iconos/ilustración propio. El coche tiene una escena tonal y más presencia; los datos se leen con claridad; los accesos, temas y desplegables responden a una intención reconocible.

El movimiento dura aproximadamente120–280ms y apoya pulsación, orientación y confirmación. No hay bucles decorativos, contadores fingidos, parallax ni señales falsas de actividad en vivo. `prefers-reduced-motion` elimina las entradas y desplazamientos ornamentales. La carga de una sección tiene un estado explícito y reintento, conservando las guardas de borradores.

No se cambian servicios, API, cálculos, persistencia, contratos nativos ni capacidades del coche. La selección visual de tema aplica la preferencia ya existente. Los registros manuales, demostración, estimaciones, datos ausentes y lecturas caducadas conservan sus etiquetas. La validación y entrega de este refinamiento se registran por separado de la aceptación física de iPhone/B10.

## Nueva dirección elegida: automóvil premium

La nueva iteración sustituye la identidad anterior por porcelana, negro profundo y azul eléctrico. La composición del coche es abierta y la navegación tiene cinco destinos directos. Se reorganizan herramientas por intención, con menos encabezados y sin perder borradores entre secciones locales.

Referencias primarias adicionales: [My Porsche, actualización del30ene2024](https://newsroom.porsche.com/en_US/2024/products/porscheextensive-update-my-porsche-app-35134.html), por la jerarquía de información y accesos frecuentes; [Lucid, guía de su aplicación](https://lucidmotors.com/knowledge/ownership/lucid-app/the-basics/the-lucid-app), por el protagonismo del vehículo y la organización de controles. Se consultó su documentación; no se probó ninguna de las dos apps ni se copió código o recursos. La primera es una referencia histórica de diseño.
