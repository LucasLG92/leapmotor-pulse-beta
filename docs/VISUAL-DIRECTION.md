# Pulse — dirección visual

## Un garaje personal
El coche y la información útil lideran. Pulse adopta un lenguaje de producto de automóvil: porcelana, tinta, azul preciso y una composición que respira. La interfaz distingue claramente información real, manual, simulada, caducada y desconocida. La representación del B10 es orientativa, no un gemelo digital ni un indicador de sensores.

## Sistema
- Manrope variable, autoalojada bajo SIL OFL1.1; fuente y licencia conservadas. Fall back al sistema. Cero solicitudes a Google Fonts desde la app.
- Fondo #f5f5f7, tinta #111318, acción #235cf3. Modo oscuro con fondo #0b0d11, superficies #16191f y azul claro contrastado.
- Ritmo de 4/8px. Paneles destacados de 24–30px, controles de 12–18px; no anidar superficies sin aportar jerarquía.
- Títulos 34–48px y peso650; datos grandes con unidades subordinadas; cuerpo legible y líneas de texto acotadas.
- Navegación de escritorio horizontal. Cinco destinos directos en dock móvil. Estados permanece en cabecera y escritorio.
- Los selectores de sección son botones con aria-pressed y un indicador inferior. Los formularios permanecen montados para conservar borradores.

## Pantallas
Coche combina una escena visual propia del B10 y un panel de autonomía, batería y acciones. Los detalles de fuente y conexión quedan accesibles bajo la información principal. Energía destaca los totales manuales; ejemplos separados. Diario usa cronología y acciones contextuales. Perfil contiene identidad compacta y opciones agrupadas. Mapa da prioridad al destino, con vacío explícito si no hay proveedor. Estados mantiene cronología y procedencia de cada observación.

## Acabado y límites
Iconos lineales propios existentes, foco visible, targets táctiles, movimiento corto respetando reduced-motion. No parallax ni animaciones continuas. No dependencias de producción añadidas. Las referencias orientan composición e interacción; nunca se copian código, plantillas, gráficos o activos externos de apps. Se conserva el sistema de confirmaciones, autenticación, caducidad y comandos físicos bloqueados.

## Evidencia
Las verificaciones se registran por release fuera del repositorio público. Validar render, foco, navegación, borradores y actualización PWA; los checks estáticos no prueban iPhone físico, VoiceOver ni comunicación con el B10.
