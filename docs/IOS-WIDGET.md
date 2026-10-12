# Widget de iPhone

La extensión original WidgetKit ofrece tamaños compacto (`systemSmall`) y mediano (`systemMedium`). El compacto muestra el primer campo elegido; el mediano, hasta tres. V12 añade cierre del coche y energía/coste registrados de los últimos30díasUTC. Cada perfil elige1–3campos de una sola familia; compact usa el primero y medium hasta tres. Energía excluye demo/evcc y dice «Calculado», vehículo conserva «Observado». No son mediciones físicas cualificadas.

La app comparte el editor web. Tras iniciar sesión y generar un código para un perfil, se enlaza el dispositivo con autorización separada `openid pulse.widget.read`. La app obtiene un permiso opaco de ese perfil. En iPhone se añade desde la pantalla de inicio; la UI explica los pasos con «Cómo añadirlo». No hay pinning programático equivalente al Android implementado.

## Privacidad y almacenamiento

La copia local está **apagada por defecto**. El grant, consentimiento e instalación aleatoria se guardan en Keychain privado (`WhenUnlockedThisDeviceOnly`); no se comparten con JavaScript ni la extensión. BFF/cookie/CSRF y tokens OAuth viven temporalmente en memoria nativa. No hay refresh token ni login silencioso.

El App Group solo contiene un snapshot saneado: campos elegidos, perfil visual, origen/demo, familia/ventana energética y fechas. El formato de proyecciónV2 conserva kind/energyWindow/generatedAt. Codable admite forma anterior solo si corresponde a vehículo con campos y orígenes originales; metadatos ausentes nunca se interpretan como energía. No guarda VIN, ubicación, cuenta, historial, cookies, CSRF ni tokens. Se escribe atómicamente, con protección completa, excluido de backup; lectura limitada a 64 KiB. Privacidad del perfil ocultada por defecto; valores `privacySensitive()` en SwiftUI.

`PulseCore` rechaza JSON duplicado, esquema/tipos/fechas inválidos, otro perfil, campos no permitidos y plazos mayores que los 15–120 minutos configurados. Revalida antes del render; compara tiempo transcurrido y reloj para fallar cerrado ante deriva/reinicio detectable. No renueva TTL al leer o fallar la red. Demo queda etiquetada y no acredita estado físico.

Los errores al leer Keychain se distinguen de un registro inexistente. Sólo `errSecItemNotFound` admite una operación idempotente sin registro; una lectura bloqueada, fallida o un registro inválido impide guardar preferencias o sustituir permisos y produce el error fijo del bridge. Los getters de presentación siguen devolviendo ausencia de credencial sin habilitarla. Una lectura no disponible tampoco acredita que deba borrarse una copia autorizada; su protección y caducidad existentes siguen vigentes. La protección `WhenUnlockedThisDeviceOnly` se mantiene, sin ampliar acceso con el teléfono bloqueado.

La generación del permiso avanza antes de cada solicitud. Foreground y background comparten lock; descartan respuestas anteriores y comprueban grant, generación y consentimiento actuales al escribir/borrar. Un preflight obsoleto no borra una copia autorizada por una solicitud nueva. Desvincular/logout borra permiso/copia locales y solicita revocación remota; sin red no se garantiza revocación del servidor inmediata.

Manifests propios: `C617.1` para tamaño/metadata del contenedor y `35F9.1` para tiempo transcurrido. No se usan para tracking ni se transmiten esas señales. Capacitor conserva su manifest; revisar el informe efectivo con Xcode antes de distribución. [Razones permitidas por Apple](https://developer.apple.com/documentation/bundleresources/app-privacy-configuration/nsprivacyaccessedapitypes/nsprivacyaccessedapitype).

## Actualización

La app solicita copia al activar consentimiento o recuperar actividad. `BGAppRefreshTask` con ID `com.example.pulse.widget.refresh` pide oportunidad después de 30 minutos; iOS decide si/cuándo la concede. Usa el grant existente: HTTPS, timeout total de 8 segundos, respuesta máxima 64 KiB y sin redirects. No autentica, renueva tokens ni consulta al fabricante. Al expirar la tarea cancela su petición.

La extensión no hace red: lee el archivo y pide timelines con caducidad/placeholder. WidgetKit controla cuándo renderiza. Puede mantener la última imagen hasta actualizarla; Data Protection y `privacySensitive` no prueban borrado inmediato de una imagen ya renderizada. No prometer refresco fijo ni invisibilidad inmediata al bloquear sin comprobarlo en el teléfono. [Timelines WidgetKit](https://developer.apple.com/documentation/widgetkit/keeping-a-widget-up-to-date), [BGTaskScheduler](https://developer.apple.com/documentation/backgroundtasks/bgtaskscheduler).

## Aceptación pendiente

Probar conexión/issuer reales, login/cancelación/callback, códigos consumidos/expirados y usuario ajeno; copia off/on, perfiles masked/visible, cambios de campos/revisión con respuestas retrasadas, logout/revinculación durante fetch; errores Keychain/App Group, grant vencido, offline, reloj/bloqueo/reinicio y límites de batería/red. Comprobar ambos tamaños, texto ampliado y VoiceOver en el iPhone 17 Pro Max.

V12 pasa23XCTest de SwiftCore en Linux y parseo de fuentes Swift. Incluye masked fresh con valoresnull, visible fresh que los exige y unavailable que los rechaza. La revisión independiente del contrato quedó cerrada tras corregir ese gate. Estas pruebas no typecheckean UIKit/WidgetKit ni validan las condiciones iOS anteriores. Compilación/firma son puertas previas en [IOS-BUILD.md](IOS-BUILD.md).

La comprobación Apple ejecuta además las regresiones de `NativeWidgetCredentialStore` con un adaptador Keychain falso: errores de lectura, consentimiento, identidad/grant, snapshot, getters y ausencia real se prueban sin acceder al Keychain del runner. No sustituye la prueba de bloqueo y protección en un iPhone ni acredita firma/distribución.
