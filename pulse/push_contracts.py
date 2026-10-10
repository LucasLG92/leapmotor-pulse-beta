"""Contrato público de avisos; material de suscripción excluido de respuestas."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class PushCapabilities(BaseModel):
    enabled: bool
    reason_code: Literal["disabled", "available"]
    application_server_key: str | None
    privacy_mode: Literal["generic"] = "generic"
    max_devices: int = 8
    scope: Literal["pwa"] = "pwa"


class PushDevice(BaseModel):
    id: str
    name: str
    endpoint_hash: str
    created_at: str
    updated_at: str
    expires_at: str
    session_active: bool
    state: Literal["active", "session_required"]
    last_attempt_at: str | None
    last_accepted_at: str | None
    last_result: Literal["never", "pending", "accepted", "failed", "uncertain"]


class PushDeviceList(BaseModel):
    items: list[PushDevice]
    max_devices: int = 8


class PushTestResult(BaseModel):
    status: Literal["accepted"]
    detail: str


class PushPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class PushKeys(PushPayload):
    p256dh: str = Field(max_length=100, repr=False)
    auth: str = Field(max_length=32, repr=False)


class PushSubscription(PushPayload):
    endpoint: str = Field(max_length=2048, repr=False)
    expirationTime: int | float | None = None
    keys: PushKeys = Field(repr=False)


class PushRegister(PushPayload):
    name: str = Field(min_length=1, max_length=60)
    subscription: PushSubscription = Field(repr=False)


class PushTest(PushPayload):
    confirmed: bool
