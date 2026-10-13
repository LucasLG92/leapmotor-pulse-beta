# Dirección visual de Pulse

Pulse es un mando personal del B10: automóvil premium, porcelana, negro profundo y azul eléctrico. El vehículo y las acciones cotidianas definen la composición. La información técnica se abre cuando hace falta, conservando origen, caducidad y límites de las funciones.

## Composición por tarea

La navegación principal tiene cinco destinos directos: Coche, Energía, Diario, Mapa y Perfil. Estados se abre desde la cabecera y también desde la navegación de escritorio. Las URL existentes se conservan.

- **Coche:** imagen propia abierta, lecturas de autonomía y batería, controles agrupados, estado recibido y procedencia. Destinos y cargas ofrecen continuidad; el diario aporta un resumen. Conexión y cargador tienen un desplegable específico.
- **Energía:** resumen personal primero. Registros y planificación tienen vistas locales; las herramientas no desplazan la tarea cotidiana.
- **Diario:** trayectos, mantenimiento y acciones tienen secciones propias.
- **Mapa:** destino y favoritos lideran. Búsqueda externa y coordenadas mantienen sus permisos, atribución y límites.
- **Perfil:** vehículo, conexiones, preferencias y datos. El enlace `/settings#preparation` abre Conexiones y conserva el foco de preparación.
- **Estados:** eventos, señales y detalles separados por intención. No se representa un centinela o cámaras como disponibles.

## Sistema visual

Tipografía de sistema, cifras tabulares, títulos breves y separación por espacio y líneas. Paleta clara `#f3f4f6`, `#ffffff`, `#10151e`, `#165dfa`; oscura `#090d14`, `#131923`, `#f5f7fc`, `#98b9ff`. Los nombres históricos de tokens se conservan por compatibilidad: `--forest` es ahora el acento azul. Ámbar comunica atención; rojo, error. El color se acompaña de texto.

La cabecera compacta deja espacio al contenido. La navegación móvil es una barra continua con cinco etiquetas visibles y safe areas. Los controles principales tienen áreas táctiles de 44–48 px como mínimo. El foco visible usa un contorno de 3 px. Las listas y los detalles sustituyen la acumulación de tarjetas anidadas.

## Interacción y continuidad

Las selecciones locales usan botones `aria-pressed` y paneles `hidden`; los formularios siguen montados y mantienen borradores y DraftGuard. Cada acción que abre un formulario selecciona antes su sección y conserva el enfoque. La navegación global, confirmaciones, reintento de carga y actualización PWA mantienen sus contratos.

La entrada de la vista y del coche es breve (280–360 ms), sin bucles ni señales que finjan actividad en directo. `prefers-reduced-motion` elimina el movimiento ornamental. El tema se elige en Perfil y conserva la preferencia existente de dispositivo/claro/oscuro.

## Referencias y autoría

La referencia de [Tobi en X](https://x.com/tobiadonadon_/status/2106430529113301163) orienta la tesis visual y la revisión del render. Las [referencias de aplicaciones](UI-REFERENCES-20261013.md) sirven para estudiar jerarquía. [My Porsche, actualización de 2024](https://newsroom.porsche.com/en_US/2024/products/porscheextensive-update-my-porsche-app-35134.html) aporta concentración de información y accesos; [Lucid](https://lucidmotors.com/knowledge/ownership/lucid-app/the-basics/the-lucid-app) aporta prioridad del coche y controles. Son referencias de diseño, no equivalencia de capacidades.

No se copian código, imágenes, fuentes o plantillas de estas aplicaciones. Pulse conserva su código y recursos propios. No se incorporan dependencias, nuevas APIs, sensores, cálculos ni persistencia.

## Validación

La revisión usa datos sintéticos y dimensiones reales de la superficie de navegador disponible. Debe comprobar ambas apariencias, seis rutas, borradores entre secciones, foco y enlaces de preparación, contraste y ausencia de desbordamiento. Un render de navegador en Linux no acredita recepción física en iPhone, VoiceOver ni pruebas con el B10. La entrega de código, despliegue estático y actualización del cliente se verifican por separado.
