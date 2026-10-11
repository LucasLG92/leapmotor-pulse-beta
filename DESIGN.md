---
name: Leapmotor Pulse
description: Cuadro personal mineral y charcoal para movilidad B10, con procedencia visible.
colors:
  canvas: "#f5f6f4"
  surface: "#ffffff"
  surface-soft: "#edf0ec"
  surface-raised: "#ffffff"
  ink: "#202722"
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
  card: "16px"
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
    rounded: "{rounded.control}"
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

Pulse combina claridad funcional, precisión y legibilidad con una identidad propia: fondo mineral o charcoal, texto nítido y verde bosque como acento funcional en acciones, selección e indicadores de estado. La jerarquía compacta organiza lecturas y tareas sin convertir cada dato en una tarjeta uniforme. Las seis superficies mantienen ritmos distintos según su trabajo: el resumen se ancla en el B10 ilustrado; energía prioriza la sesión y su coste; navegación centra la búsqueda y el mapa; actividad ordena registros; protección explica límites y cambios; ajustes agrupa perfil e integraciones con separadores.

La representación original CGI del B10 acompaña al perfil en seis acabados elegibles y conserva una aclaración visible de que el aspecto es orientativo. La interfaz distingue preparación, demo y lecturas mediante etiquetas, fechas y fuente. Los detalles plegables guardan información ampliada y alternativas sin ocultar el estado principal.

**Key Characteristics:**
- Neutros minerales y charcoal con acentos verdes de uso funcional.
- Jerarquía tipográfica del sistema y superficies con separadores finos.
- Identidad visual del B10 acompañada por estado, fecha y procedencia.

## Colors

La paleta clara usa blanco mineral, superficies blancas y tinta verde-gris; charcoal invierte las superficies y aclara el texto. Jade bosque comunica acción, menta acompaña selección, ámbar marca atención y rojo indica error.

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

El espacio de trabajo limita el contenido a `1360px` y usa un rail lateral fijo en escritorio. Bajo `700px`, el rail se reemplaza por cuatro accesos inferiores: Coche, Energía, Mapa y Actividad. Estados y Ajustes quedan accesibles desde los iconos de la cabecera. Si el contenedor de navegación mide menos de `18em`, los cuatro accesos pasan a un selector compacto que permite llegar a las seis secciones. Se respetan las áreas seguras del dispositivo. Los formularios a dos columnas se apilan bajo `430px`, y cada ruta ajusta sus propios grids: resumen pasa de escena y tarea en paralelo a una columna; energía separa cargador, coste, registros y formulario; navegación apila resultados y mapa; actividad distingue diario y mantenimiento; protección prioriza observaciones y límites; ajustes reordena grupos e integraciones. El ritmo reutiliza separadores y espacios de `8–24px`; las tarjetas conservan padding cercano a `20px` cuando el patrón de ruta usa tarjetas.

## Elevation & Depth

La profundidad es principalmente tonal y estructural: superficies claras/charcoal y bordes finos agrupan contenido. Menús, avisos flotantes y diálogos usan una sombra suave; la navegación inferior tiene una sombra superior discreta. La sombra no sustituye a los separadores ni crea elevación uniforme en todas las secciones.

### Shadow Vocabulary
- **Menú y diálogo:** `0 14px 42px rgb(25 30 38 / 7%)` en claro y `0 16px 44px rgb(0 0 0 / 24%)` en charcoal.
- **Navegación inferior:** `0 -8px 24px rgb(25 30 38 / 5%)`.

## Shapes

Los radios distinguen escala y función: campos de `9px`, controles de `10px`, paneles compactos de `12px`, tarjetas de `16px` y estados en píldora. Líneas de `1px` y foco visible de `3px` con desplazamiento de `3px` definen el borde; el selector de acabado usa muestras circulares de `21px` dentro de objetivos de `44px`.

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
- **Corner Style:** `16px` en tarjetas; el patrón varía por pantalla.
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
El resumen centra autonomía y batería junto a una ilustración lateral CGI de fondo transparente. Procedencia y fecha preceden a cierre, clima, carga, odómetro y acciones; los datos desconocidos no dibujan un nivel de batería ficticio. El nombre del color y el selector mantienen la personalización en contexto. El lateral reúne estado del cargador, última sesión detectada, último registro personal y conexión del vehículo. Las fechas, energía y costes disponibles permanecen visibles; recepción, método y límites del cálculo se despliegan bajo demanda. Debajo, listas y separadores reúnen energía, último trayecto, mantenimiento y cambios de estado. Los seis acabados se muestran como guía visual, no como imagen o identificación oficial del fabricante.

### Registros de energía
El cargador y el criterio del coste se presentan como bloques informativos abiertos. Los registros distinguen origen manual y demo con etiquetas y señales de color; el gráfico por sesión y la analítica mensual acompañan a una lista editable. Cada registro permite consultar fin, método guardado y tarifa histórica sin sustituirla por la configuración actual. Las horas de recepción del cargador quedan identificadas como recepción del servidor, sin atribuirles una medición física. El desglose PVPC, el análisis técnico no cualificado y las alternativas de fechas se consultan en detalles o selecciones del formulario.

### Mapa y diario
La navegación combina búsqueda y destino con una superficie de mapa, resultados y tarjetas de estación; en pantallas estrechas pasa a una sola columna. Actividad separa trayectos, historial de acciones y mantenimiento en listas con fechas y acciones de edición claras.

### Historial de estados
La pantalla de protección ordena observaciones, límites y eventos; las horas y fuentes quedan asociadas a cada cambio. Los estados y avisos conservan etiquetas explícitas para distinguir lecturas de registros ficticios.

## Do's and Don'ts

### Do:
- **Do** usar las variables semánticas existentes para canvas, superficies, tinta, líneas, acentos y temas.
- **Do** mantener fechas, fuente y estado junto a las lecturas, con los detalles ampliados accesibles bajo demanda.
- **Do** presentar el CGI original del B10 con sus seis acabados y la nota de representación orientativa.
- **Do** mantener visibles el foco, los controles táctiles de al menos `44px` y el ajuste `prefers-reduced-motion` ya presentes.

### Don't:
- **Don't** convertir verde o menta en el fondo dominante; son acentos funcionales sobre base mineral o charcoal.
- **Don't** sustituir la ilustración propia por logotipos o fotografía oficial del fabricante.
- **Don't** tratar el perfil como telemetría ni una etiqueta de demo como conexión o control físico.
- **Don't** aplanar las seis rutas en un patrón idéntico de tarjetas; conserva la composición específica de cada tarea.
