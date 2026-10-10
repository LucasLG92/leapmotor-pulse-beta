# HTTPS y acceso desde un móvil

La instalación rápida usa loopback y autenticación local. Para instalar la PWA en otro dispositivo necesitas un origen HTTPS válido y autenticación OIDC. No cambies solo el bind a `0.0.0.0` ni publiques el modo local: la validación del servidor lo rechaza.

Este manual asume Docker Engine en Linux y un proxy HTTPS en el mismo host. Si el proxy está dentro de otro contenedor o servidor, su `127.0.0.1` es distinto: prepara una red privada y reglas de acceso adecuadas. El instalador no modifica DNS, firewall, router, Caddy ni Authelia.

## Configuración privada

Registra en tu proveedor OIDC un cliente web confidencial propio, con Authorization Code, PKCE S256, autenticación `client_secret_basic`, scopes `openid profile email` y callback exacto `https://pulse.example.org/api/auth/callback`. La URL del issuer debe coincidir exactamente con su discovery, incluido el puerto si lo tiene. No reutilices el secreto de otra aplicación.

Guarda los valores en un archivo privado **fuera del repositorio**, por ejemplo `~/.config/pulse/runtime.env`, con carpeta700/archivo600. El ejemplo público está en [runtime.env.example](../deploy/community/runtime.env.example). Usa tus propios dominio, issuer, cliente, secreto y usuario permitido; el secreto no debe quedar vacío. No pegues este archivo en issues, capturas o salidas de `docker compose config`.

Con configuración completa, validar sin mostrar valores:

```sh
docker compose --env-file "$HOME/.config/pulse/runtime.env" -p leapmotor-pulse-beta-public -f compose.yaml -f deploy/community/compose.local.yaml config --quiet
```

La puesta en marcha manual utiliza esos mismos argumentos y `up --build -d --no-deps --wait --wait-timeout 90 pulse`. Después de esta personalización, no ejecutes `bash install.sh --install` o `bash install.sh --stop`: el instalador usa exclusivamente la configuración local segura. Para detener el despliegue personalizado, usa el mismo comando Compose con `stop pulse`.

## Proxy HTTPS

Ejemplo mínimo para Caddy ejecutándose en el mismo host. Sustituye el dominio y configura sus DNS/TLS siguiendo la documentación de Caddy:

```caddyfile
pulse.example.org {
    reverse_proxy 127.0.0.1:8780
}
```

Pulse gestiona OIDC y su sesión propia. No admite autenticación a través de cabeceras `Remote-User`. No expongas directamente8780 ni añadas bypass de cookies, TLS o autenticación para solucionar un error.

Comprueba: shell/readiness200, API privada401 sin sesión y con identidad de proxy falsa, login permitido, rechazo con otra cuenta real, logout y reentrada. Prueba desde datos móviles si quieres acceso fuera de tu red. Un200 en readiness no acredita autenticación, protección del proxy o funcionamiento del coche.

## Avisos, apps y widgets

Las opciones avanzadas requieren configuración separada. [MOBILE.md](MOBILE.md) describe PWA/Web Push y los requisitos de OAuth nativo y asociación de plataforma. No instales un APK configurado para el servidor de otra persona: compila tu variante con tus parámetros públicos y firma. Los secretos OIDC de servidor nunca van dentro de la app.

Referencias: [Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy), [Authelia OIDC](https://www.authelia.com/configuration/identity-providers/openid-connect/provider/).
