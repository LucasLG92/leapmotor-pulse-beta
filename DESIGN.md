---
name: Leapmotor Pulse
description: Cuadro personal claro y charcoal para el perfil Leapmotor B10 Pro Max, con procedencia visible.
colors:
  canvas: "#f5f6f8"
  surface: "#ffffff"
  surface-soft: "#edf0f3"
  surface-raised: "#ffffff"
  ink: "#202429"
  muted: "#5d6570"
  muted-strong: "#454c56"
  line: "#dfe3e8"
  line-strong: "#7b8794"
  forest: "#17634a"
  forest-hover: "#104e3a"
  forest-soft: "#e5f1eb"
  mint: "#b8ddc8"
  accent: "#945b20"
  accent-soft: "#f4ecdc"
  danger: "#a54034"
  danger-soft: "#f8e9e5"
  focus: "#237658"
  on-primary: "#ffffff"
  on-accent: "#ffffff"
  on-danger: "#ffffff"
  selection-ink: "#202b25"
  charcoal-canvas: "#202226"
  charcoal-surface: "#282b30"
  charcoal-surface-soft: "#32363c"
  charcoal-surface-raised: "#2d3036"
  charcoal-ink: "#f0f2f5"
  charcoal-muted: "#b7bec8"
  charcoal-muted-strong: "#dce1e7"
  charcoal-line: "#41464e"
  charcoal-line-strong: "#808b9b"
  charcoal-forest: "#a2d6bd"
  charcoal-forest-hover: "#bee7d3"
  charcoal-forest-soft: "#2a3e35"
  charcoal-mint: "#a2d6bd"
  charcoal-accent: "#f0bd78"
  charcoal-accent-soft: "#413423"
  charcoal-danger: "#f09a8d"
  charcoal-danger-soft: "#442a27"
  charcoal-focus: "#f0bd78"
  charcoal-on-primary: "#202b25"
  charcoal-on-accent: "#282b30"
  charcoal-on-danger: "#282b30"
typography:
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "2rem"
    fontWeight: 620
    lineHeight: 1.1
    letterSpacing: "-.035em"
  headline:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: "-.035em"
  title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "1.125rem"
    fontWeight: 590
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
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: ".04em"
rounded:
  compact: "8px"
  field: "9px"
  control: "10px"
  panel: "12px"
  card: "16px"
  vehicle-hero: "20px"
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
  input:
    backgroundColor: "{colors.surface-raised}"
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
  vehicle-color-option:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "9px 12px"
---

# Design System: Leapmotor Pulse

## Overview

**Creative North Star: "Cabina clara de movilidad"**

Pulse presenta el B10 Pro Max con una lectura serena: jerarquía y precisión de un cuadro de movilidad, aire y tipografía del sistema al estilo de las referencias Apple y Google, y datos de carga fáciles de localizar como en Tesla. La interfaz traduce esas referencias en componentes propios, con superficies claras y carbón y acentos verdes limitados a acciones y selección.

La ilustración lateral del B10 y sus seis acabados seleccionables dan identidad al perfil. La preparación sin vehículo y la demo se distinguen con texto y estado persistente; una lectura ausente no se sustituye por cifras estimadas.

**Key Characteristics:**
- Jerarquía compacta, neutral y legible en superficies claras y charcoal.
- El modelo B10 Pro Max y sus seis acabados orientan la personalización visual.
- Los estados de preparación y demo conservan su procedencia visible.

## Colors

La paleta parte de grises fríos y carbón; el jade guía acciones y selección, el ámbar identifica demo y estados de atención, y el rojo queda reservado a errores.

### Primary
- **Jade de acción** (`colors.forest`): botones primarios, selección activa e iconos de estado. `forest-soft` crea su superficie tenue; el tema charcoal emplea los valores propios de la familia definidos en los tokens.

### Secondary
- **Ámbar de atención** (`colors.accent`): demo y estados que requieren distinguirse; siempre acompaña una etiqueta textual.

### Tertiary
- **Rojo de error** (`colors.danger`): validación y errores. Sus fondos siguen el tema activo.

### Neutral
- **Canvas y superficies** (`colors.canvas`, `colors.surface`, `colors.surface-soft`, `colors.surface-raised`): fondo de página, tarjetas, agrupaciones tenues y controles.
- **Tinta y texto secundario** (`colors.ink`, `colors.muted`, `colors.muted-strong`): lectura principal, metadatos y énfasis secundario.
- **Divisores** (`colors.line`, `colors.line-strong`): separación ligera y contornos de controles. La segunda escala tiene valores propios por tema, definidos en los tokens.
- **Selección de texto**: `selection-ink` se mantiene sobre `mint`, según la regla CSS global.

**The Labelled State Rule.** Color acompaña al texto de estado; nunca comunica por sí solo demo, conexión, éxito o error.

## Typography

**Display Font:** Sistema (`-apple-system`, `BlinkMacSystemFont`, `Segoe UI`, sans-serif).
**Body Font:** La misma pila del sistema.
**Label/Mono Font:** La pila de sistema para etiquetas; `ui-monospace`, `SFMono-Regular`, `Menlo`, monospace solo para códigos.

**Character:** Sobria y funcional. Tamaños, pesos, cifras tabulares y tracking crean jerarquía sin descargar fuentes.

### Hierarchy
- **Display** (620, `2rem`, `1.1`): cifras principales de batería y autonomía.
- **Headline** (600, `1.5rem`, `1.5`): título de página; se compacta en móvil.
- **Title** (590, `1.125rem`, `1.5`): encabezados de secciones.
- **Body** (400, `1rem`, `1.5`): texto principal y explicación.
- **Label** (650, `.875rem`, `1.2` en botones): controles; metadatos y captions bajan a `.75rem`.

## Layout

En escritorio, un rail fijo de `14rem` acompaña el espacio de trabajo, cuyo contenido llega hasta `1360px`. El rail pasa a `13rem` hasta `860px`; bajo `700px` desaparece y la navegación usa barra inferior con área segura. En móvil hay cinco elementos: cuatro destinos directos y «Más», que agrupa Protección y Ajustes.

La barra inferior cambia a un menú compacto cuando su contenedor mide menos de `23em`. La consulta es relativa al contenedor, cuyo `font-size` es `.75rem`; no es un breakpoint de viewport. Resumen adapta la tarjeta B10 a una columna bajo `700px`; Ajustes coloca ilustración y selector en una columna bajo `1100px`. El ritmo observado usa huecos de `16px` entre tarjetas y padding de `18–28px` en la vista del vehículo.

## Elevation & Depth

La profundidad viene de superficies tonales y bordes finos; las tarjetas llevan una sombra muy tenue y los menús elevan con la sombra compartida, distinta en charcoal. La barra inferior tiene una sombra superior sutil. No aparecen blur ni gradientes en estas superficies.

## Shapes

Tarjetas de `16px` y hero del vehículo de `20px`; controles y selector de color de `10px`, campos de `9px`, paneles compactos de `12px` y estados en píldora. El selector usa muestras circulares de `24px`. Los contornos son de `1px`; el foco general dibuja `3px` con desplazamiento de `3px` y el selector `2px` con desplazamiento de `3px`.

## Components

### Buttons
- **Shape:** esquinas moderadas (`10px`), altura base `48px`; variantes compactas mantienen `44px` mínimo.
- **Primary:** jade de acción, texto de contraste, padding `10px 16px`.
- **Secondary / Quiet:** superficie y borde para la secundaria; fondo transparente para quiet. Hover de la secundaria y quiet usa superficie jade tenue.
- **Hover / Focus:** transición de color de `140ms`; hover y active ajustan brillo. Foco visible con contorno del tema.

### Chips
- **Style:** la demo usa texto ámbar sobre su fondo tenue, borde y punto de estado.
- **State:** conexión, demo y preparación se nombran explícitamente; el color acompaña la etiqueta.

### Cards / Containers
- **Corner Style:** `16px`, con padding habitual `20–22px`.
- **Background:** superficie del tema activo; secciones relacionadas pueden usar la superficie tenue.
- **Shadow Strategy:** elevación corta en reposo y sombra compartida en menús.
- **Border:** divisor de `1px`.
- **Internal Padding:** `20px` global; la tarjeta del vehículo ajusta entre `18px` y `28px`.

### Inputs / Fields
- **Style:** control de `48px`, borde fuerte, fondo raised y radio `9px`; texto de entrada usa tamaño de cuerpo.
- **Focus:** contorno visible del tema; el selector B10 mantiene su propio contorno al recibir foco interno.

### Navigation
- Rail vertical en escritorio; barra de cuatro destinos directos y menú «Más» en móvil. El activo combina fondo jade tenue y texto jade.
- Con contenedor estrecho, `details` expone las seis secciones con etiquetas completas.

### B10 Pro Max
La vista principal usa la ilustración lateral del B10 con relación `627 / 280` y un pie que aclara que el acabado es orientativo. Ajustes permite elegir seis colores de carrocería o «Sin elegir»; cada opción muestra muestra cromática, nombre y selección. Las seis imágenes WebP y las muestras representan acabados, no lecturas ni fotografía oficial.

## Do's and Don'ts

### Do:
- **Do** use las variables semánticas del tema para canvas, superficies, texto, líneas y acentos.
- **Do** conservar etiquetas visibles para preparación, demo y conexión, y mostrar solo lecturas entregadas.
- **Do** presentar el B10 Pro Max con su ilustración y selector de seis acabados; mantener el acabado como orientativo.
- **Do** conservar foco visible, áreas de control de al menos `44px` y el comportamiento `prefers-reduced-motion` presente en CSS.

### Don't:
- **Don't** convertir jade o mint en el fondo dominante; la base actual es neutra clara o charcoal.
- **Don't** sustituir la ilustración B10 por un SUV genérico, logos o fotografía oficial.
- **Don't** presentar valores de preparación como telemetría, ni acciones de demo como controles físicos.
- **Don't** añadir blur, cristal o gradientes decorativos a tarjetas y navegación.
