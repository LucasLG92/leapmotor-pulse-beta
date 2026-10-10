"""Configuración explícita: local cerrado u OIDC, nunca identidad de cabeceras."""

from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit
import re

from pulse.mqtt_policy import MqttConfig
from pulse.push_policy import PushConfig
from pulse.evcc import EvccConfig
from pulse.navigation_config import NavigationConfig
from pulse.abrp_policy import AbrpConfig


def _https_url(value: str, field_name: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ("", "/")):
        raise ValueError(f"{field_name} debe ser una URL HTTPS de origen, sin ruta ni credenciales.")
    return value.rstrip("/")


@dataclass(frozen=True)
class Config:
    auth_mode: str = "local"
    public_url: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = field(default="", repr=False)
    allowed_users: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]")
    session_ttl: int = 8 * 60 * 60
    max_sessions: int = 512
    native_enabled: bool = False
    native_app_enabled: bool = False
    native_client_id: str = ""
    native_audience: str = ""
    native_introspection_url: str = ""
    native_userinfo_url: str = ""
    native_introspection_client_id: str = ""
    native_introspection_client_secret: str = field(default="", repr=False)
    revoke_widget_grants_on_start: bool = False
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    push: PushConfig = field(default_factory=PushConfig)
    evcc: EvccConfig = field(default_factory=EvccConfig)
    navigation: NavigationConfig = field(default_factory=NavigationConfig)
    abrp: AbrpConfig = field(default_factory=AbrpConfig)

    def __post_init__(self):
        if not isinstance(self.mqtt, MqttConfig):
            raise ValueError("La configuración MQTT debe ser explícita y válida.")
        if not isinstance(self.push, PushConfig):
            raise ValueError("La configuración push debe ser explícita y válida.")
        if not isinstance(self.evcc, EvccConfig):
            raise ValueError("La configuración evcc debe ser explícita y válida.")
        if not isinstance(self.navigation, NavigationConfig):
            raise ValueError("La configuración de mapas debe ser explícita y válida.")
        if not isinstance(self.abrp, AbrpConfig):
            raise ValueError("La configuración ABRP debe ser explícita y válida.")
        if self.auth_mode not in {"local", "oidc"}:
            raise ValueError("PULSE_AUTH_MODE debe ser local u oidc.")
        if not self.hosts or any("*" in host or "/" in host for host in self.hosts):
            raise ValueError("PULSE_HOSTS debe enumerar hosts exactos.")
        if not 60 <= self.session_ttl <= 86400 or not 1 <= self.max_sessions <= 4096:
            raise ValueError("Límites de sesión inválidos.")
        if self.auth_mode == "oidc":
            _https_url(self.public_url, "PULSE_PUBLIC_URL")
            _https_url(self.oidc_issuer, "PULSE_OIDC_ISSUER")
            if not self.oidc_client_id or not self.oidc_client_secret or not self.allowed_users:
                raise ValueError("OIDC requiere cliente, secreto y usuarios permitidos explícitos.")
            if urlsplit(self.public_url).hostname not in self.hosts:
                raise ValueError("El host público debe figurar en PULSE_HOSTS.")
        if type(self.native_enabled) is not bool:
            raise ValueError("PULSE_NATIVE_ENABLED debe ser booleano.")
        if type(self.native_app_enabled) is not bool:
            raise ValueError("PULSE_NATIVE_APP_ENABLED debe ser booleano.")
        if type(self.revoke_widget_grants_on_start) is not bool:
            raise ValueError("La revocación al iniciar debe ser booleana.")
        if self.native_app_enabled and not self.native_enabled:
            raise ValueError("PULSE_NATIVE_APP_ENABLED requiere PULSE_NATIVE_ENABLED.")
        if self.native_enabled:
            if self.auth_mode != "oidc":
                raise ValueError("Los dispositivos nativos requieren OIDC y HTTPS.")
            for value in (self.native_client_id, self.native_introspection_client_id):
                if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
                    raise ValueError("Los identificadores de cliente nativo deben ser explícitos y válidos.")
            if self.native_client_id in {self.oidc_client_id, self.native_introspection_client_id}:
                raise ValueError("El cliente público nativo debe ser distinto del cliente confidencial.")
            if (not isinstance(self.native_audience, str) or not 1 <= len(self.native_audience) <= 255
                    or any(character.isspace() or ord(character) < 32 or ord(character) == 127
                           for character in self.native_audience)):
                raise ValueError("PULSE_NATIVE_AUDIENCE debe identificar explícitamente la API Pulse.")
            if self.native_audience in {self.native_client_id, self.oidc_client_id, self.native_introspection_client_id}:
                raise ValueError("La audiencia de la API debe ser distinta de los clientes OIDC.")
            issuer = urlsplit(self.oidc_issuer)
            for value in (self.native_introspection_url, self.native_userinfo_url):
                endpoint = urlsplit(value)
                if (endpoint.scheme != "https" or endpoint.netloc != issuer.netloc
                        or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                        or not endpoint.path.startswith("/") or endpoint.path == "/"):
                    raise ValueError("La verificación nativa requiere destinos HTTPS del issuer.")
            if not self.native_introspection_client_secret:
                raise ValueError("La introspección requiere un cliente confidencial.")

    @property
    def cookie_name(self) -> str:
        return "__Host-pulse_session" if self.auth_mode == "oidc" else "pulse_session"

    @property
    def callback_url(self) -> str:
        return self.public_url.rstrip("/") + "/api/auth/callback"

    @classmethod
    def from_env(cls, *, hosts: list[str] | None = None):
        return cls(
            navigation=NavigationConfig.from_env(),
            abrp=AbrpConfig.from_env(),
            auth_mode=os.environ.get("PULSE_AUTH_MODE", "local"),
            public_url=os.environ.get("PULSE_PUBLIC_URL", ""),
            oidc_issuer=os.environ.get("PULSE_OIDC_ISSUER", ""),
            oidc_client_id=os.environ.get("PULSE_OIDC_CLIENT_ID", ""),
            oidc_client_secret=os.environ.get("PULSE_OIDC_CLIENT_SECRET", ""),
            allowed_users=tuple(x.strip() for x in os.environ.get("PULSE_ALLOWED_USERS", "").split(",") if x.strip()),
            hosts=tuple(hosts) if hosts is not None else tuple(x.strip() for x in os.environ.get(
                "PULSE_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if x.strip()),
            native_enabled=_native_flag(os.environ.get("PULSE_NATIVE_ENABLED", "false")),
            native_app_enabled=_native_flag(os.environ.get("PULSE_NATIVE_APP_ENABLED", "false"),
                                            "PULSE_NATIVE_APP_ENABLED"),
            native_client_id=os.environ.get("PULSE_NATIVE_CLIENT_ID", ""),
            native_audience=os.environ.get("PULSE_NATIVE_AUDIENCE", ""),
            native_introspection_url=os.environ.get("PULSE_NATIVE_INTROSPECTION_URL", ""),
            native_userinfo_url=os.environ.get("PULSE_NATIVE_USERINFO_URL", ""),
            native_introspection_client_id=os.environ.get("PULSE_NATIVE_INTROSPECTION_CLIENT_ID", ""),
            native_introspection_client_secret=os.environ.get("PULSE_NATIVE_INTROSPECTION_CLIENT_SECRET", ""),
            revoke_widget_grants_on_start=_native_flag(os.environ.get("PULSE_REVOKE_WIDGET_GRANTS_ON_START", "false")),
            mqtt=MqttConfig.from_env(),
            push=PushConfig.from_env(),
            evcc=EvccConfig.from_env(),
        )


def _native_flag(value: str, setting_name: str = "PULSE_NATIVE_ENABLED") -> bool:
    if value not in {"true", "false"}:
        raise ValueError(f"{setting_name} debe ser true o false.")
    return value == "true"
