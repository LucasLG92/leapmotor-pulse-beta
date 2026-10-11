---
name: Leapmotor Pulse
description: Cuadro personal mineral y charcoal para movilidad B10, con procedencia visible.
colors:
  canvas: "#f7f8f6"
  surface: "#ffffff"
  surface-soft: "#edf1ed"
  surface-raised: "#ffffff"
  ink: "#202623"
  muted: "#606961"
  muted-strong: "#48554b"
  line: "#dfe4df"
  line-strong: "#89968c"
  forest: "#246c4b"
  forest-hover: "#1c543b"
  forest-soft: "#e8f2eb"
  mint: "#b8ddc8"
  accent: "#945b20"
  accent-soft: "#f4ecdc"
  danger: "#a54034"
  danger-soft: "#f8e9e5"
  focus: "#246c4b"
  on-primary: "#ffffff"
  on-accent: "#ffffff"
  on-danger: "#ffffff"
  charcoal-canvas: "#1c1f21"
  charcoal-surface: "#24282a"
  charcoal-surface-soft: "#2c3133"
  charcoal-surface-raised: "#24282a"
  charcoal-ink: "#f0f3f0"
  charcoal-muted: "#b5bdb7"
  charcoal-muted-strong: "#d8e1da"
  charcoal-line: "#3b4240"
  charcoal-line-strong: "#7e8b82"
  charcoal-forest: "#9cddba"
  charcoal-forest-hover: "#bee7d3"
  charcoal-forest-soft: "#2c3d33"
  charcoal-accent: "#f0bd78"
  charcoal-accent-soft: "#413423"
  charcoal-danger: "#f09a8d"
  charcoal-danger-soft: "#442a27"
  charcoal-focus: "#f0bd78"
  charcoal-on-primary: "#17271c"
  charcoal-on-accent: "#24282a"
  charcoal-on-danger: "#24282a"
typography:
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "2.25rem"
    fontWeight: 650
    lineHeight: 1.08
    letterSpacing: "-.035em"
  headline:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.75rem"
    fontWeight: 590
    lineHeight: 1.12
    letterSpacing: "-.035em"
  title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.125rem"
    fontWeight: 650
    lineHeight: 1.5
    letterSpacing: "-.025em"
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: ".875rem"
    fontWeight: 650
    lineHeight: 1.2
  caption:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: ".75rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  field: "9px"
  control: "10px"
  panel: "12px"
  card: "20px"
  pill: "99px"
components:
  button-primary:
    backgroundColor: "{colors.forest}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.muted-strong}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.on-danger}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.field}"
    padding: "0 12px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.card}"
    padding: "20px"
  nav-active:
    backgroundColor: "{colors.forest-soft}"
    textColor: "{colors.forest}"
    typography: "{typography.label}"
    rounded: "16px"
    padding: "0 13px"
  status-demo:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: "0 10px"
---

# Design System: Leapmotor Pulse

## Overview

**Creative North Star: "Cabina clara de movilidad"**

Pulse combina claridad funcional, precisión y legibilidad con una identidad propia: lienzo mineral o charcoal, texto nítido y verde bosque como acento funcional en acciones, selección e indicadores de estado. La jerarquía compacta organiza lecturas y tareas sin convertir cada dato en una tarjeta uniforme. Las seis superficies mantienen ritmos distintos según su trabajo: Coche equilibra perfil, estado y contexto; Energía separa sesiones de cargador y registros personales; Mapa centra búsqueda y destino; Actividad ordena trayectos y mantenimiento; Estados reúne cambios y lecturas recibidas; Ajustes agrupa perfil e integraciones con separadores.

La representación original CGI del B10 acompaña al perfil en seis acabados elegibles y conserva una aclaración visible de que el aspecto es orientativo. La interfaz distingue preparación, demo y lecturas mediante etiquetas, fechas y fuente. En Coche se muestran la fuente y la hora observada; el detalle amplía fuentes por campo y horas de recepción. El estado actual respeta la caducidad temporal canónica calculada por Pulse: una lectura fuera de su ventana se marca como caducada y no se presenta como vigente, aunque sus snapshots sigan en Estados.

**Key Characteristics:**
- Neutros minerales y charcoal con acentos verdes de uso funcional.
- Jerarquía tipográfica del sistema y superficies con separadores finos.
- Identidad visual del B10 acompañada por estado, fecha y procedencia.

## Colors

La paleta clara usa un lienzo mineral `#f7f8f6`, superficies blancas y tinta verde-gris `#202623`; la superficie secundaria `#edf1ed` agrupa contenido sin elevarlo como tarjeta. Charcoal invierte las superficies y aclara el texto. Jade bosque comunica acción, menta acompaña selección, ámbar marca atención y rojo indica error.

### Primary
- **Verde bosque:** `colors.forest` guía acciones, enlaces, selección y foco en el tema claro; el tema oscuro cambia a la variante verde clara definida para charcoal.
- **Verde tenue:** `colors.forest-soft` suaviza estados activos y superficies de apoyo sin teñir toda la pantalla.

### Secondary
- **Ámbar de atención:** `colors.accent` y `colors.accent-soft` distinguen demo y avisos junto a una etiqueta legible.
- **Menta de selección:** `colors.mint` acompaña la selección de texto y estados suaves.

### Tertiary
- **Rojo de error:** `colors.danger` y `colors.danger-soft` identifican errores y acciones destructivas.

### Neutral
- **Blanco mineral:** `colors.canvas`, `colors.surface` y `colors.surface-soft` separan el lienzo, las superficies principales y los grupos secundarios.
- **Tinta verde-gris:** `colors.ink`, `colors.muted` y `colors.muted-strong` jerarquizan contenido, metadatos y texto secundario.
- **Líneas de salvia:** `colors.line` y `colors.line-strong` separan listas y delimitan controles.
- **Carbón profundo:** `colors.charcoal-canvas`, `colors.charcoal-surface` y `colors.charcoal-surface-soft` forman el tema oscuro; la familia charcoal equivalente aporta tinta, líneas y acentos propios.

**The Labelled State Rule.** El color acompaña una etiqueta textual para demo, conexión, éxito o error; no lleva el estado por sí solo.

## Typography

**Display Font:** Sistema (`-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, sans-serif).
**Body Font:** La misma pila del sistema.
**Label/Mono Font:** La pila de sistema en controles; el navegador aporta la fuente monoespaciada propia de elementos `code`.

**Character:** Sobria y práctica. Los pesos, el tracking moderado y las cifras tabulares crean jerarquía sin descargar fuentes.

### Hierarchy
- **Display** (650, `2.25rem`, `1.08`): lectura destacada de autonomía.
- **Headline** (590–600, `1.75rem` en las vistas principales): títulos de pantalla, con tamaño adaptado por ruta y ancho.
- **Title** (620–660, `1.125rem`): encabezados de sección y panel.
- **Body** (400, `1rem`, `1.5`): texto y explicación.
- **Label** (560–700, `.875rem`): controles y metadatos; captions bajan a `.75rem`.

## Layout

El espacio de trabajo limita el contenido a `1320px` y usa un rail lateral fijo en escritorio. Bajo `700px`, el rail se reemplaza por cuatro accesos inferiores: Coche, Energía, Mapa y Actividad. Estados y Ajustes quedan accesibles desde los iconos de la cabecera. Si el contenedor de navegación mide menos de `18em`, los cuatro accesos pasan a un selector compacto que permite llegar a las seis secciones. Se respetan las áreas seguras del dispositivo. Los formularios a dos columnas se apilan bajo `430px`, y cada ruta ajusta sus propios grids: Coche pasa de escena y tarea en paralelo a una columna; Energía ordena cargador y criterio de coste, deja abiertos los totales personales y mantiene aparte el panel plegable de registros; Mapa apila resultados y mapa; Actividad distingue diario y mantenimiento; Estados separa cambios recibidos de snapshots; Ajustes reordena grupos e integraciones. El ritmo reutiliza separadores y espacios de `8–24px`; las tarjetas conservan padding cercano a `20px` cuando el patrón de ruta usa tarjetas.

## Elevation & Depth

La profundidad es principalmente tonal y estructural: superficies claras/charcoal y bordes finos agrupan contenido. Menús, avisos flotantes y diálogos usan una sombra suave; la navegación inferior tiene una sombra superior discreta. La sombra no sustituye a los separadores ni crea elevación uniforme en todas las secciones.

### Shadow Vocabulary
- **Menú y diálogo:** `0 14px 42px rgb(25 30 38 / 7%)` en claro y `0 16px 44px rgb(0 0 0 / 24%)` en charcoal.
- **Navegación inferior:** `0 -8px 24px rgb(25 30 38 / 5%)`.

## Shapes

Los radios distinguen escala y función: campos de `9px`, controles de `10px`, paneles compactos de `12px`, navegación de escritorio de `16px`, tarjetas de `20px` y estados en píldora. Líneas de `1px` y foco visible de `3px` con desplazamiento de `3px` definen el borde; el selector de acabado usa muestras circulares de `21px` dentro de objetivos de `44px`.

## Components

### Buttons
- **Shape:** esquinas suaves (`10px`) y altura mínima de `48px`; variantes pequeñas conservan `44px`.
- **Primary:** fondo verde bosque, texto claro y padding `10px 16px`.
- **Secondary / Quiet:** superficie con contorno o fondo transparente; hover usa superficie verde tenue.
- **Danger:** rojo y texto claro para acciones destructivas, con el estado nombrado.
- **Hover / Focus:** transición de color y borde de `140ms`; active reduce brillo; foco con contorno visible y desplazamiento.

### Chips
- **Style:** etiquetas compactas y redondeadas; demo usa texto ámbar, fondo tenue, borde y punto.
- **State:** el texto nombra preparación, conexión, demo o resultado y conserva el significado cuando se usa charcoal.

### Cards / Containers
- **Corner Style:** `20px` en tarjetas; el patrón varía por pantalla.
- **Background:** superficie del tema activo, separada con línea fina.
- **Shadow Strategy:** casi plana; profundidad adicional en menús y diálogos.
- **Border:** línea de `1px` cuando el componente es una tarjeta; otras secciones usan reglas horizontales o verticales.
- **Internal Padding:** alrededor de `20px` en tarjetas comunes; algunas superficies de ruta se presentan abiertas y sin contenedor.

### Inputs / Fields
- **Style:** fondo de control, borde fuerte, radio `9px` y altura mínima de `48px`.
- **Focus:** contorno del tema de `3px` con separación de `3px`; el error se etiqueta junto al campo.

### Navigation
- En escritorio, rail lateral vertical con el destino activo en verde tenue. En móvil, la barra inferior ofrece cuatro destinos con iconos SVG lineales; Estados y Ajustes se abren desde los iconos de cabecera. Si el contenedor mide menos de `18em`, un selector compacto `details` da acceso a las seis rutas.

### B10 Pro Max
El resumen equilibra autonomía y batería con una ilustración lateral CGI de fondo transparente, cuyo aspecto se identifica como orientativo. Procedencia y hora observada acompañan el estado de cierre, clima, carga y odómetro; si la respuesta canónica de Pulse declara caducidad temporal, no se reutilizan esos valores como estado actual. El detalle muestra fuente por campo y separa hora observada de recepción por proveedor y por Pulse. El nombre del color y su selector siguen junto al perfil; las acciones permanecen identificadas como demo y bloqueadas fuera de ella. El lateral mantiene aparte estado del cargador, última sesión detectada y registros personales con fechas, energía y costes. Debajo, listas y separadores reúnen energía, trayecto, mantenimiento y acceso al historial de Estados. Los seis acabados se muestran como guía visual, no como imagen o identificación oficial del fabricante.

### Registros de energía
El cargador y el criterio del coste se presentan abiertos y separados. Antes del historial plegable, los totales de energía y coste de registros manuales quedan siempre visibles cuando Pulse está conectado; en demo aparece su grupo propio, sin sumar sesiones evcc. Distancia y consumo por trayecto se muestran solo con datos del diario ficticio, rotulados como simulados y sin presentarlos como consumo real del vehículo. El historial conserva la lista editable, el gráfico por sesión y la analítica mensual por origen; cada registro permite consultar fin, método guardado y tarifa histórica sin sustituirla por la configuración actual. Las horas de recepción del cargador quedan identificadas como recepción del servidor, sin atribuirles una medición física. El desglose PVPC, el análisis técnico no cualificado y las alternativas de fechas se consultan en detalles o selecciones del formulario; las lecturas recibidas se mantienen separadas de la energía de sesiones.

### Mapa y diario
La navegación combina búsqueda y destino con una superficie de mapa, resultados y tarjetas de estación; en pantallas estrechas pasa a una sola columna. Actividad separa trayectos, historial de acciones y mantenimiento en listas con fechas y acciones de edición claras.

### Estados
La pantalla permite recorrer por páginas el historial retenido que Pulse expone, con cambios observados y snapshots de lecturas recibidas en series separadas. Un cambio compara dos lecturas válidas de cierre o carga; no confirma una acción física. Cada fila conserva la fuente, la hora observada y las horas de recepción de ambas lecturas, mientras que los snapshots muestran fecha, señal y fuente por campo. El diagnóstico actual distingue la vigencia temporal canónica de Pulse de la vigencia física, que sigue sin cualificar; las lecturas caducadas permanecen consultables en el historial, pero no se presentan como actuales. La demo ofrece eventos ficticios con su propia etiqueta y no se confunde con las lecturas recibidas.

## Do's and Don'ts

### Do:
- **Do** usar las variables semánticas existentes para canvas, superficies, tinta, líneas, acentos y temas.
- **Do** mantener fechas, fuente y estado junto a las lecturas, y distinguir horas observadas y recibidas en los historiales.
- **Do** reservar distancia y consumo por trayecto a los datos demo, identificándolos como simulados y sin atribuirlos al vehículo.
- **Do** presentar el CGI original del B10 con sus seis acabados y la nota de representación orientativa.
- **Do** mantener visibles el foco, los controles táctiles de al menos `44px` y el ajuste `prefers-reduced-motion` ya presentes.

### Don't:
- **Don't** convertir verde o menta en el fondo dominante; son acentos funcionales sobre base mineral o charcoal.
- **Don't** sustituir la ilustración propia por logotipos o fotografía oficial del fabricante.
- **Don't** tratar el perfil como telemetría ni una etiqueta de demo como conexión o control físico.
- **Don't** aplanar las seis rutas en un patrón idéntico de tarjetas; conserva la composición específica de cada tarea.
