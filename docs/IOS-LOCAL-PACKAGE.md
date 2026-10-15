# Preparar una IPA iOS pública para firma local

`scripts/configure-ios-ipa.py` permite insertar la configuración pública de Pulse en la IPA genérica auditada del 15 de octubre de 2026. La herramienta solo prepara otro archivo unsigned: no firma, instala, sube ni inicia sesión en Apple.

## Entrada y comando

La entrada aceptada está fijada por SHA-256 a `31cbe14937738616844c4036760717fb677ba83ba42030f4ba6c63d633d7d88c`. Hay que pasar ese mismo valor en `--expected-sha256`; el helper comprueba el hash real antes de interpretar el ZIP. No acepta IPA firmadas ni permite sustituir la fuente por otro archivo que solo parezca genérico.

La configuración es un archivo UTF-8 local con estas seis claves exactas. Para empezar, crea `ios-native-public.properties` con valores ficticios `.example.invalid` o sustituye cada valor por el endpoint público autorizado de tu instalación:

```properties
pulse.apiOrigin=https://pulse.example.invalid
pulse.issuer=https://auth.example.invalid
pulse.authorizationEndpoint=https://auth.example.invalid/oidc/authorization
pulse.tokenEndpoint=https://auth.example.invalid/oidc/token
pulse.clientId=pulse-public-example
pulse.audience=https://pulse.example.invalid/api
```

| Clave | Campo de `App.app/Info.plist` |
| --- | --- |
| `pulse.apiOrigin` | `PulseAPIOrigin` |
| `pulse.issuer` | `PulseOIDCIssuer` |
| `pulse.authorizationEndpoint` | `PulseAuthorizationEndpoint` |
| `pulse.tokenEndpoint` | `PulseTokenEndpoint` |
| `pulse.clientId` | `PulseClientID` |
| `pulse.audience` | `PulseAudience` |

El helper deriva `PulseCallback` como `<apiOrigin>/native/oauth/callback`. Valida HTTPS, ausencia de credenciales en las URL, query, fragmento y traversal; exige que los endpoints OAuth compartan autoridad y estén bajo la ruta del issuer. El parser admite solo esos seis nombres de clave y no imprime sus valores. Eso rechaza campos extra como `clientSecret`, pero no puede reconocer un secreto pegado por error en uno de los seis campos; quien prepare el archivo debe usar únicamente valores públicos.

Ejemplo de invocación desde la raíz del workspace:

```sh
python3 work/pulse/scripts/configure-ios-ipa.py \
  outputs/leapmotor-pulse-ios-phase2-unsigned-20261015-876c451.ipa \
  --expected-sha256 31cbe14937738616844c4036760717fb677ba83ba42030f4ba6c63d633d7d88c \
  --public-config ios-native-public.properties \
  --output outputs/leapmotor-pulse-ios-phase2-configured.ipa
```

`--app-group` es opcional y por defecto conserva `group.com.lucas.pulse`. Si se especifica otro identificador, se escribe igual en `PulseAppGroup` de la app y de `PulseWidget.appex`. El valor solo expresa la selección del operador; el helper no puede confirmar que el equipo, el perfil de provisión o la cuenta Apple tengan ese grupo habilitado. La firma posterior debe conceder exactamente ese App Group a la app y al widget.

La salida es el nuevo archivo indicado en `--output` y un recibo privado adyacente llamado `<output>.receipt.json`. Ambos se crean con permisos `0600`; el directorio temporal se crea con `0700`. Se rechazan salidas existentes y las publicaciones no reemplazan archivos. Cada archivo se prepara y enlaza atómicamente; ante una interrupción del proceso entre la publicación del recibo y la IPA, puede quedar un recibo huérfano `0600`, pero no una IPA parcial. Consérvalo y usa rutas nuevas tras revisar el estado. El recibo no contiene los valores de configuración ni endpoints. El empaquetador conserva el contenido descomprimido de cada entrada salvo las dos `Info.plist` que modifica, y confirma la integridad del ZIP de salida.

## Límites de seguridad y compatibilidad

El parser no extrae archivos al disco. Limita el ZIP a 64 MiB, 512 entradas, 64 MiB por entrada y 256 MiB descomprimidos; rechaza rutas inseguras o duplicadas, enlaces y archivos especiales, entradas cifradas, ratios de compresión excesivos y material de firma o provisión. También requiere los bundle IDs, versión, build y ejecutables arm64 iPhoneOS esperados, sin `LC_CODE_SIGNATURE`.

La configuración del plist no crea ni modifica entitlements. `PulseAppGroup` en ambos Info.plist debe coincidir, después de firmar, con `com.apple.security.application-groups` y con los perfiles incluidos para cada bundle. La elegibilidad de App Groups, Keychain, WidgetKit y OAuth no se demuestra con una IPA unsigned; requiere revisión de la firma y prueba en el dispositivo. El recibo declara expresamente esos puntos como pendientes.

SideStore no adapta automáticamente el App Group de Pulse. En su operación de firma, el código fuente de [`ResignAppOperation.swift` en SideStore, revisión `12ff39c6b6c0ccd8180f0834a893a4bbaf7bcd31`](https://github.com/SideStore/SideStore/blob/12ff39c6b6c0ccd8180f0834a893a4bbaf7bcd31/SideStore/Core/Operations/PipelineOperations/ResignAppOperation.swift#L915-L928) solo remapea `NSExtensionFileProviderDocumentGroup` cuando existe ese campo y el perfil ofrece un grupo compatible. No cambia `PulseAppGroup` ni concede por sí mismo el entitlement de App Groups que Pulse necesita para compartir el widget.

Este paso de preparación puede ejecutarse en Linux y no requiere un Mac. La firma personal gratuita sigue requiriendo una cuenta Apple local, emparejamiento por USB y Anisette; hay que validar las capabilities que permite esa cuenta y el perfil generado. La distribución por App Store o TestFlight requiere la membresía de pago de Apple Developer Program y su flujo de firma. No se ha demostrado una ruta de firma e instalación de Pulse sin Mac, ni que WidgetKit, App Groups, Keychain u OAuth funcionen con Personal Team. Esta herramienta no ha firmado ni instalado la IPA.
