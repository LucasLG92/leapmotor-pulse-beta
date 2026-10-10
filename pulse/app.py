"""API del mismo origen, SPA estática y propietario único de almacenamiento."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException

from pulse import __version__
from pulse.auth import Sessions, build_oidc, register_auth
from pulse.config import Config
from pulse.collector import Collector, quality
from pulse.contracts import (ChargePage, ChargeQuote, ChargerStatus, ChargerSessionPage, CommandHistory, EnergyAnalytics, StateResponse, TelemetryPage, TelemetryResponse, WidgetProfileList,
                             WidgetProfileResult)
from pulse.coordinator import Coordinator
from pulse.domain import DomainError
from pulse.electricity import Electricity
from pulse.mqtt_bridge import MqttBridge
from pulse.native import NativeGateway, register_native
from pulse.providers.base import UnconfiguredProvider
from pulse.providers.b10_bootstrap import ManagedB10Provider
from pulse.security import SecurityMiddleware
from pulse.static import PublicStaticFiles
from pulse.push_service import PushService, register_push
from pulse.charger import ChargerReader
from pulse.contracts import ChargerPricingHistoryPage
from pulse.navigation import Navigation
from pulse.abrp import AbrpBridge
from pulse.contracts import NavigationStatus, NavigationSearch, NavigationChargers, NavigationTile, ObservedEventPage


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class SettingsPayload(Payload):
    vehicle_name: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    tariff_eur_kwh: float | None = Field(default=None, ge=0, le=5)
    home_label: str | None = Field(default=None, max_length=120)
    pricing_method: Literal["manual", "pvpc"] | None = None


class TogglePayload(Payload):
    enabled: bool


class NavigationSearchPayload(Payload):
    query: str = Field(min_length=3, max_length=120)
    consent: bool


class NavigationChargersPayload(Payload):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    consent: bool


class NavigationTilePayload(Payload):
    z: int = Field(ge=0, le=18)
    x: int = Field(ge=0, le=262143)
    y: int = Field(ge=0, le=262143)
    consent: bool


class CommandPayload(Payload):
    command: str = Field(max_length=32)
    confirmed: bool


class ChargePayload(Payload):
    started_at: str = Field(max_length=64)
    kwh: float = Field(gt=0, le=500)
    ended_at: str | None = Field(default=None, max_length=64)
    pricing_method: Literal["manual", "pvpc"] | None = None


class ChargeEditPayload(Payload):
    started_at: str | None = Field(default=None, max_length=64)
    kwh: float | None = Field(default=None, gt=0, le=500)
    tariff_eur_kwh: float | None = Field(default=None, ge=0, le=5)
    ended_at: str | None = Field(default=None, max_length=64)
    pricing_method: Literal["manual", "pvpc"] | None = None


class MaintenancePayload(Payload):
    title: str = Field(max_length=120)
    due_date: str = Field(max_length=10)


class MaintenanceEditPayload(Payload):
    title: str | None = Field(default=None, max_length=120)
    due_date: str | None = Field(default=None, max_length=10)
    completed: bool | None = None


class ImportPayload(Payload):
    confirmed: bool
    data: dict


class WidgetProfilePayload(Payload):
    name: str = Field(min_length=1, max_length=60)
    fields: list[Literal["battery_pct", "range_km", "charging", "locked", "energy_kwh", "energy_cost_eur"]] = Field(min_length=1, max_length=3)
    appearance: Literal["system", "light", "dark"] = "system"
    privacy_mode: Literal["masked", "visible"] = "masked"
    max_age_minutes: int = Field(default=60, ge=15, le=120)


class WidgetProfileEditPayload(Payload):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    fields: list[Literal["battery_pct", "range_km", "charging", "locked", "energy_kwh", "energy_cost_eur"]] | None = Field(default=None, min_length=1, max_length=3)
    appearance: Literal["system", "light", "dark"] | None = None
    privacy_mode: Literal["masked", "visible"] | None = None
    max_age_minutes: int | None = Field(default=None, ge=15, le=120)


def create_app(db_path: str | Path | None = None, *, name: str | None = None,
               hosts: list[str] | None = None, trusted_proxies: list[str] | None = None,
               config: Config | None = None, static_dir: str | Path | None = None,
               oidc_client=None, provider=None, pvpc_fetcher=None, native_transport=None,
               mqtt_transport=None, push_transport=None, evcc_transport=None,
               navigation_transport=None, abrp_transport=None, energy_curve_reader=None) -> FastAPI:
    if trusted_proxies:
        raise ValueError("Pulse autentica mediante OIDC; no acepta identidad del proxy.")
    config = config or Config.from_env(hosts=hosts)
    coordinator = Coordinator(Path(db_path or os.environ.get("PULSE_DB", "./data/pulse.sqlite3")))
    brand = name or os.environ.get("PULSE_NAME", "Leapmotor Pulse")
    sessions = Sessions(config)
    oidc = oidc_client if oidc_client is not None else build_oidc(config)
    dist = Path(static_dir) if static_dir else Path(__file__).resolve().parent.parent / "apps/web/dist"
    vehicle_provider = provider if provider is not None else UnconfiguredProvider()
    if not callable(getattr(vehicle_provider, "connection_status", None)):
        raise ValueError("El proveedor no implementa connection_status().")
    # Únicamente un bootstrap autorizado puede inyectar un cliente configurado.
    # El arranque normal no construye SDKs, lee material privado ni programa consultas.
    push = PushService(config.push, coordinator, sessions, config.cookie_name, transport=push_transport)
    abrp = AbrpBridge(config.abrp, coordinator, transport=abrp_transport,
                      on_status_change=lambda _status: coordinator._publish())

    def observe(stored):
        push.observe(stored)
        abrp.observe(stored)

    collector = Collector(vehicle_provider, coordinator, on_stored=observe) if provider is not None else None
    electricity = Electricity(coordinator, **({"fetcher": pvpc_fetcher} if pvpc_fetcher is not None else {}))
    native_gateway = NativeGateway(config, coordinator, transport=native_transport)
    mqtt = MqttBridge(config.mqtt, coordinator, transport=mqtt_transport,
                      on_status_change=lambda _status: coordinator._publish())
    charger = ChargerReader(config.evcc, coordinator, transport=evcc_transport,
                            electricity=electricity, energy_curve_reader=energy_curve_reader)
    navigation = Navigation(config.navigation, coordinator.path.parent / "map-cache",
                            transport=navigation_transport)

    @asynccontextmanager
    async def lifespan(application):
        await coordinator.start()
        application.state.store = coordinator.store
        try:
            if config.revoke_widget_grants_on_start:
                await coordinator.call("revoke_all_widget_grants", write=True)
            await electricity.start()
            await push.start()
            await abrp.start()
            if collector:
                await collector.start()
            await mqtt.start()
            await charger.start()
            yield
        finally:
            try:
                await abrp.close()
            finally:
                try:
                    try:
                        await charger.close()
                    finally:
                        await mqtt.close()
                finally:
                    try:
                        await electricity.close()
                    finally:
                        try:
                            if collector:
                                await collector.close()
                        finally:
                            try:
                                if isinstance(vehicle_provider, ManagedB10Provider):
                                    vehicle_provider.close()
                            finally:
                                try:
                                    await push.close()
                                finally:
                                    try:
                                        await navigation.close()
                                    finally:
                                        await coordinator.close()

    app = FastAPI(title="Leapmotor Pulse", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.coordinator, app.state.sessions, app.state.config = coordinator, sessions, config
    app.state.oidc = oidc
    app.state.collector = collector
    app.state.provider = vehicle_provider
    app.state.electricity = electricity
    app.state.mqtt = mqtt
    app.state.push = push
    app.state.charger = charger
    app.state.navigation = navigation
    app.state.abrp = abrp
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.hosts), www_redirect=False)
    app.add_middleware(SecurityMiddleware, config=config, sessions=sessions, native_gateway=native_gateway)
    register_auth(app, config, sessions, oidc)

    @app.get("/api/navigation/status", response_model=NavigationStatus)
    async def navigation_status():
        return navigation.status()

    @app.post("/api/navigation/places", response_model=NavigationSearch)
    async def navigation_places(payload: NavigationSearchPayload):
        return await navigation.search(payload.query, payload.consent)

    @app.post("/api/navigation/chargers", response_model=NavigationChargers)
    async def navigation_chargers(payload: NavigationChargersPayload):
        return await navigation.chargers(payload.latitude, payload.longitude, payload.consent)

    @app.post("/api/navigation/tiles", response_model=NavigationTile)
    async def navigation_tile(payload: NavigationTilePayload, request: Request):
        # Only a same-origin referer is forwarded, without a path or private query.
        referer = request.headers.get("referer")
        expected = config.public_url.rstrip("/") if config.auth_mode == "oidc" else str(request.base_url).rstrip("/")
        if referer:
            from urllib.parse import urlsplit
            try:
                supplied = urlsplit(referer)
                referer = expected + "/" if f"{supplied.scheme}://{supplied.netloc}" == expected else None
            except ValueError:
                referer = None
        return await navigation.tile(payload.z, payload.x, payload.y, payload.consent, referer=referer)

    @app.exception_handler(DomainError)
    async def expected_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status,
                            headers={"Cache-Control": "no-store"})

    @app.exception_handler(RequestValidationError)
    async def invalid_payload(request, error):
        # Pydantic incluye input en sus errores; no reflejamos datos privados.
        return JSONResponse({"detail": "Revisa los campos enviados: hay datos inválidos o no permitidos."},
                            status_code=422, headers={"Cache-Control": "no-store"})

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({"detail": "Recurso no disponible."}, status_code=error.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        return JSONResponse({"detail": "No se pudo completar la operación. Inténtalo de nuevo."},
                            status_code=500, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def ready():
        ready = coordinator.store is not None and (dist / "index.html").is_file()
        return JSONResponse({"status": "ready" if ready else "not_ready"}, status_code=200 if ready else 503)

    @app.get("/api/state", response_model=StateResponse)
    async def state():
        context = await coordinator.call("state_context", brand, __version__)
        result = context["state"]
        result["integrations"] = {"mqtt": mqtt.status(), "abrp": abrp.status()}
        # Configuration is not qualification. This deliberately exposes only
        # flags: no provider reads, destinations, material or extra store calls.
        result["setup"] = {
            "evidence_scope": "configuration_only", "auth_mode": config.auth_mode,
            "vehicle_reader_enabled": collector is not None and not isinstance(vehicle_provider, UnconfiguredProvider),
            "charger_enabled": config.evcc.enabled,
            "interval_energy_enabled": config.evcc.energy_history_enabled,
            "native_app_enabled": config.native_app_enabled,
            "native_widgets_enabled": config.native_enabled,
            "web_push_enabled": config.push.enabled,
            "home_assistant_enabled": config.mqtt.enabled,
            "abrp_enabled": config.abrp.enabled,
            "navigation_enabled": config.navigation.enabled,
            "physical_commands_enabled": False,
        }
        if result["mode"] == "demo":
            result["connection"] = {"status": "demo", "reason": "Datos simulados. El coche no está conectado."}
        else:
            result["connection"] = vehicle_provider.connection_status()
            info = telemetry_info(context["snapshot"])
            result["telemetry"] = info
            snapshot = info["latest"]
            if collector and snapshot:
                result["mode"] = "telemetry_read_only"
                values = snapshot["vehicle"]
                if info["quality"]["temporal_status"] == "unavailable":
                    values = {field: None for field in values}
                result["vehicle"] = {"name": result["settings"]["vehicle_name"],
                                     "model": result["settings"]["model"], **values,
                                     "observed_at": snapshot["observed_at"]}
                result["connection"]["freshness"] = info["quality"]["freshness"]
                for command in result["commands"]:
                    command["reason"] = "Controles físicos pendientes de validación individual."
        return result

    def telemetry_info(snapshot):
        return {"latest": snapshot, "quality": quality(snapshot, datetime.now(timezone.utc)),
                "collector": collector.status() if collector else {"running": False, "enabled": False}}

    async def telemetry_state():
        return telemetry_info(await coordinator.call("telemetry_latest"))

    @app.get("/api/telemetry", response_model=TelemetryResponse)
    async def telemetry():
        return await telemetry_state()

    @app.get("/api/telemetry/history", response_model=TelemetryPage)
    async def telemetry_history(limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        return await coordinator.call("telemetry_history", limit, offset)

    @app.get("/api/protection/observations", response_model=ObservedEventPage)
    async def observed_events(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000)):
        return await coordinator.call("observed_events", limit, offset)

    @app.put("/api/settings")
    async def settings(values: SettingsPayload):
        await coordinator.call("update_settings", values.model_dump(exclude_unset=True), write=True)
        electricity.settings_changed()
        charger.settings_changed()
        return {"ok": True}

    @app.post("/api/demo")
    async def demo(values: TogglePayload):
        abrp.invalidate()
        try:
            await coordinator.call("set_demo", values.enabled, write=True)
        finally:
            # A pending collector callback may arrive while SQLite was awaited.
            abrp.invalidate()
        return {"ok": True}

    @app.put("/api/integrations/abrp")
    async def abrp_consent(values: TogglePayload):
        await abrp.set_consent(values.enabled)
        return {"ok": True}

    @app.post("/api/commands")
    async def command(values: CommandPayload):
        return await coordinator.call("command", values.command, values.confirmed, write=True)

    @app.get("/api/commands/history", response_model=CommandHistory)
    async def history(limit: int = Query(default=50, ge=1, le=100)):
        return {"items": await coordinator.call("command_history", limit)}

    @app.put("/api/protection")
    async def protection(values: TogglePayload):
        await coordinator.call("set_protection", values.enabled, write=True)
        return {"ok": True}

    @app.post("/api/protection/event")
    async def event(values: Payload):
        return {"ok": True, "event": await coordinator.call("simulate_event", write=True)}

    @app.get("/api/charges", response_model=ChargePage)
    async def charges(limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        return await coordinator.call("list_charges", limit, offset)

    @app.get("/api/analytics/energy", response_model=EnergyAnalytics)
    async def energy_analytics(days: int = Query(default=30, ge=1, le=365)):
        return await coordinator.call("energy_analytics", days)

    @app.get("/api/charger", response_model=ChargerStatus)
    async def charger_status():
        return await charger.status()

    @app.get("/api/charger/sessions", response_model=ChargerSessionPage)
    async def charger_sessions(limit: int = Query(default=20, ge=1, le=100),
                               offset: int = Query(default=0, ge=0, le=1000)):
        return await charger.sessions(limit, offset)

    @app.get("/api/charger/pricing-history", response_model=ChargerPricingHistoryPage)
    async def charger_pricing_history(
        session_id: str = Query(pattern=r"^[0-9a-f]{32}$", max_length=32),
        limit: int = Query(default=10, ge=1, le=20),
        offset: int = Query(default=0, ge=0, le=2048),
    ):
        return await charger.pricing_history(session_id, limit, offset)

    @app.post("/api/charges/quote", response_model=ChargeQuote)
    async def quote(values: ChargePayload):
        data = values.model_dump(exclude_unset=True)
        if data.get("pricing_method") not in (None, "pvpc"):
            raise DomainError("La vista previa por tramos requiere PVPC.")
        return await electricity.quote(data)

    @app.post("/api/charges")
    async def charge(values: ChargePayload):
        data = values.model_dump(exclude_unset=True)
        settings = await coordinator.call("get_settings")
        data.setdefault("pricing_method", settings["pricing_method"])
        snapshot = await electricity.quote(data) if data["pricing_method"] == "pvpc" else None
        result = await coordinator.call("add_charge", data, pricing_snapshot=snapshot, write=True)
        return {"ok": True, "charge": result}

    @app.put("/api/charges/{item_id}")
    async def edit_charge(item_id: str, values: ChargeEditPayload):
        data = values.model_dump(exclude_unset=True)
        if not data:
            raise DomainError("La carga contiene campos vacíos o no admitidos.")
        current = await coordinator.call("get_charge", item_id)
        method = data.get("pricing_method", "pvpc" if current["pricing"] else "manual")
        data.setdefault("pricing_method", method)
        snapshot = None
        if method == "pvpc":
            if "tariff_eur_kwh" in data:
                raise DomainError("PVPC calcula la tarifa por tramos; no admite un precio fijo.")
            quote_data = {field: data.get(field, current[field]) for field in ("started_at", "ended_at", "kwh")}
            snapshot = await electricity.quote(quote_data)
        result = await coordinator.call("update_charge", item_id, data,
                                         pricing_snapshot=snapshot, write=True)
        return {"ok": True, "charge": result}

    @app.delete("/api/charges/{item_id}")
    async def delete_charge(item_id: str, values: Payload):
        return await coordinator.call("delete_charge", item_id, write=True)

    @app.post("/api/maintenance")
    async def maintenance(values: MaintenancePayload):
        result = await coordinator.call("add_maintenance", values.model_dump(), write=True)
        return {"ok": True, "maintenance": result}

    @app.put("/api/maintenance/{item_id}")
    async def edit_maintenance(item_id: str, values: MaintenanceEditPayload):
        result = await coordinator.call("update_maintenance", item_id, values.model_dump(exclude_unset=True), write=True)
        return {"ok": True, "maintenance": result}

    @app.delete("/api/maintenance/{item_id}")
    async def delete_maintenance(item_id: str, values: Payload):
        return await coordinator.call("delete_maintenance", item_id, write=True)

    def owner_id(request: Request) -> str:
        # La identidad procede de la sesión verificada; nunca del JSON, URL o proxy.
        record = request.scope.get("pulse.session")
        if not record or not record.user or not sessions.active(record):
            raise DomainError("Recarga Pulse e inicia sesión para gestionar tus widgets.", 401)
        return record.user["id"]

    register_native(app, config, coordinator, native_gateway, owner_id, sessions)
    register_push(app, push, owner_id)

    @app.get("/api/widget-profiles", response_model=WidgetProfileList)
    async def widget_profiles(request: Request):
        return await coordinator.call("list_widget_profiles", owner_id(request))

    @app.post("/api/widget-profiles", response_model=WidgetProfileResult)
    async def create_widget_profile(request: Request, values: WidgetProfilePayload):
        profile = await coordinator.call("create_widget_profile", owner_id(request), values.model_dump(), write=True)
        return {"ok": True, "profile": profile}

    @app.put("/api/widget-profiles/{profile_id}", response_model=WidgetProfileResult)
    async def edit_widget_profile(request: Request, profile_id: str, values: WidgetProfileEditPayload):
        profile = await coordinator.call("update_widget_profile", owner_id(request), profile_id,
                                         values.model_dump(exclude_unset=True), write=True)
        return {"ok": True, "profile": profile}

    @app.delete("/api/widget-profiles/{profile_id}")
    async def delete_widget_profile(request: Request, profile_id: str, values: Payload):
        await coordinator.call("delete_widget_profile", owner_id(request), profile_id, write=True)
        return {"ok": True}

    @app.get("/api/export")
    async def export(request: Request):
        data = json.dumps(await coordinator.call("export_data", owner_id=owner_id(request)),
                          ensure_ascii=False, allow_nan=False, indent=2)
        return Response(data, media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="leapmotor-pulse-backup.json"'})

    @app.post("/api/import")
    async def restore(request: Request, values: ImportPayload):
        if not values.confirmed:
            raise DomainError("Confirma la sustitución de datos antes de restaurar.", 409)
        abrp.invalidate()
        restored = False
        try:
            await coordinator.restore(values.data, owner_id=owner_id(request))
            restored = True
        finally:
            abrp.invalidate(clear_consent=restored)
        electricity.settings_changed()
        charger.settings_changed()
        return {"ok": True}

    @app.get("/api/events")
    async def events(request: Request):
        if len(coordinator.subscribers) >= 32:
            raise DomainError("Hay demasiadas conexiones de actualización abiertas.", 429)
        record = request.scope.get("pulse.session")
        if not record:
            raise DomainError("Recarga la página antes de suscribirte.", 401)
        queue = asyncio.Queue(maxsize=1)

        async def stream():
            coordinator.subscribers.add(queue)
            try:
                yield f'event: state\ndata: {{"revision":{coordinator.revision}}}\n\n'
                while sessions.active(record):
                    if await request.is_disconnected():
                        break
                    try:
                        revision = await asyncio.wait_for(queue.get(), timeout=15)
                        if not sessions.active(record):
                            break
                        yield f'id: {revision}\nevent: state\ndata: {{"revision":{revision}}}\n\n'
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                coordinator.subscribers.discard(queue)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "X-Accel-Buffering": "no", "Cache-Control": "no-store"})

    public_files = PublicStaticFiles(directory=dist, check_dir=False)
    if (dist / "assets").is_dir():
        app.mount("/assets", PublicStaticFiles(directory=dist / "assets"), name="assets")

    @app.head("/sw.js", include_in_schema=False)
    @app.get("/sw.js")
    async def worker(request: Request):
        if not (dist / "sw.js").is_file():
            raise DomainError("La PWA todavía no está compilada.", 503)
        response = await public_files.get_response("sw.js", request.scope)
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.head("/manifest.webmanifest", include_in_schema=False)
    @app.get("/manifest.webmanifest")
    async def manifest(request: Request):
        if not (dist / "manifest.webmanifest").is_file():
            raise DomainError("La PWA todavía no está compilada.", 503)
        return await public_files.get_response("manifest.webmanifest", request.scope)

    @app.head("/{path:path}", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def shell(path: str, request: Request):
        if path in {"icon.svg", "icon-180.png", "icon-192.png", "icon-512.png", "apple-touch-icon.png"} and (dist / path).is_file():
            return await public_files.get_response(path, request.scope)
        if path not in {"", "index.html", "energy", "navigation", "activity", "protection", "settings"}:
            raise DomainError("Página no encontrada.", 404)
        if not (dist / "index.html").is_file():
            raise DomainError("La interfaz todavía no está compilada.", 503)
        return await public_files.get_response("index.html", request.scope)

    return app
