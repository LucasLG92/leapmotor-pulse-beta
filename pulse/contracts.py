"""Contratos de respuesta públicos; OpenAPI describe los datos del navegador."""

from typing import Any, Literal
from pydantic import BaseModel, Field, model_serializer


class NavigationStatus(BaseModel):
    enabled: bool
    source: Literal["OpenStreetMap"]
    reason: Literal["ready", "disabled"]
    attribution: Literal["© OpenStreetMap contributors"]
    places_provider: Literal["nominatim", "photon_private"] = "nominatim"
    private_place_search: bool = False

    @model_serializer(mode="wrap")
    def serialize_navigation_status(self, handler):
        result = handler(self)
        if self.places_provider == "nominatim" and not self.private_place_search:
            result.pop("places_provider", None)
            result.pop("private_place_search", None)
        return result


class NavigationPlace(BaseModel):
    id: str = Field(max_length=80)
    name: str = Field(max_length=160)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class NavigationStation(NavigationPlace):
    operator: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=160)
    connectors: list[str] = Field(max_length=12)
    power_kw: float | None = Field(default=None, ge=0, le=2000, allow_inf_nan=False)
    distance_m: int = Field(ge=0, le=5100)


class NavigationSearch(BaseModel):
    source: Literal["OpenStreetMap Nominatim", "OpenStreetMap Photon"]
    fetched_at: str
    cached: bool
    places: list[NavigationPlace] = Field(max_length=5)


class NavigationChargers(BaseModel):
    source: Literal["OpenStreetMap Overpass"]
    fetched_at: str
    cached: bool
    truncated: bool
    stations: list[NavigationStation] = Field(max_length=60)


class NavigationTile(BaseModel):
    data_url: str = Field(pattern=r"^data:image/png;base64,[A-Za-z0-9+/]+=*$", max_length=180000)
    cached: bool


class User(BaseModel):
    id: str
    name: str


class SessionResponse(BaseModel):
    csrf_token: str
    authenticated: bool
    user: User | None
    login_url: str | None


class NativeAppSessionResponse(BaseModel):
    ok: bool
    expires_in: int


class AppInfo(BaseModel):
    name: str
    version: str


class Settings(BaseModel):
    vehicle_name: str
    model: str
    tariff_eur_kwh: float
    home_label: str
    pricing_method: Literal["manual", "pvpc"]
    vehicle_color: Literal[
        "starry_night_blue",
        "midnight_purple",
        "tundra_grey",
        "metallic_black",
        "stellar_silver",
        "lunar_white",
    ] | None


class Vehicle(BaseModel):
    name: str
    model: str
    battery_pct: float | None
    range_km: float | None
    odometer_km: float | None
    interior_temperature_c: float | None = None
    battery_min_temperature_c: float | None = None
    front_left_pressure_kpa: float | None = None
    front_right_pressure_kpa: float | None = None
    rear_left_pressure_kpa: float | None = None
    rear_right_pressure_kpa: float | None = None
    vehicle_security_code: int | None = Field(default=None, ge=0, le=255, strict=True)
    sentry_code: int | None = Field(default=None, ge=0, le=255, strict=True)
    locked: bool | None
    climate_on: bool | None
    charging: bool | None
    observed_at: str | None


class PriceInterval(BaseModel):
    started_at: str
    ended_at: str
    kwh: float
    price_eur_kwh: float
    cost_eur: float
    price_fetched_at: str


class PricingSnapshot(BaseModel):
    method: Literal["pvpc"]
    accuracy: Literal["estimated_uniform"]
    region: Literal["peninsula"]
    source: Literal["REE REData PVPC 1001"]
    taxes_included: Literal[False]
    calculated_at: str
    intervals: list[PriceInterval]


class EnergyInterval(BaseModel):
    started_at: str
    ended_at: str
    kwh: float


class EnergyDistribution(BaseModel):
    # Association IDs remain in the private archive, not in UI/API snapshots.
    version: Literal[1]
    source_id: str
    source_version: str
    basis: Literal["charger_total"]
    intervals: list[EnergyInterval]


class TemporalPricingSnapshot(PricingSnapshot):
    accuracy: Literal["estimated_intervals"]
    energy_distribution: EnergyDistribution


class ChargeQuote(BaseModel):
    cost_eur: float
    tariff_eur_kwh: float
    pricing: PricingSnapshot | TemporalPricingSnapshot = Field(discriminator="accuracy")


class ChargeCreationRequest(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    expires_at: str


class ManualJourneyStop(BaseModel):
    started_at: str
    ended_at: str


class ManualJourney(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    revision: int = Field(ge=1)
    source: Literal["manual"]
    coverage: Literal["unknown"]
    started_at: str
    ended_at: str
    from_label: str | None
    to_label: str | None
    notes: str | None = Field(max_length=2000)
    tags: list[str] = Field(max_length=8)
    distance_km: float | None = Field(ge=0, le=50_000, allow_inf_nan=False)
    energy_kwh: float | None = Field(ge=0, le=5_000, allow_inf_nan=False)
    stops: list[ManualJourneyStop] = Field(max_length=32)
    duration_min: float = Field(ge=0, allow_inf_nan=False)
    paused_min: float = Field(ge=0, allow_inf_nan=False)
    active_min: float = Field(ge=0, allow_inf_nan=False)
    consumption_kwh_100km: float | None = Field(ge=0, allow_inf_nan=False)
    created_at: str
    updated_at: str


class ManualJourneyPage(BaseModel):
    items: list[ManualJourney]
    total: int = Field(ge=0)


class ManualJourneyCreationRequest(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    expires_at: str


class ManualJourneyPeriod(BaseModel):
    started_at: str
    ended_at: str
    days: Literal[30, 90, 365]
    timezone: Literal["UTC"]


class ManualJourneyTotals(BaseModel):
    count: int = Field(ge=0)
    distance_km: float = Field(ge=0, allow_inf_nan=False)
    energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    known_distance_count: int = Field(ge=0)
    known_energy_count: int = Field(ge=0)
    paired_count: int = Field(ge=0)
    paired_distance_km: float = Field(ge=0, allow_inf_nan=False)
    paired_energy_kwh: float = Field(ge=0, allow_inf_nan=False)
    consumption_kwh_100km: float | None = Field(ge=0, allow_inf_nan=False)


class ManualJourneyMonth(ManualJourneyTotals):
    month_key: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class ManualJourneyReport(BaseModel):
    source: Literal["manual"]
    coverage: Literal["unknown"]
    period: ManualJourneyPeriod
    totals: ManualJourneyTotals
    months: list[ManualJourneyMonth]


class ChargerObservation(BaseModel):
    received_at: str
    loadpoint: str
    connected: bool
    charging: bool
    power_w: float | None
    charged_kwh: float | None
    active_seconds: float | None


class ChargerSession(BaseModel):
    id: str
    upstream_id: int
    loadpoint: str
    started_at: str
    ended_at: str
    kwh: float
    active_seconds: float | None
    received_at: str
    pvpc: ChargeQuote | None = None
    pricing_status: Literal["ready", "pending", "ineligible", "disabled"] = "disabled"


class ChargerEnergyStatus(BaseModel):
    enabled: bool
    status: Literal["disabled", "waiting", "received", "error"]
    reason_code: str
    message: str
    last_success_at: str | None
    retained_intervals: int
    total_kwh: float
    first_started_at: str | None
    last_ended_at: str | None
    last_received_at: str | None
    retention_limit: int
    validation: Literal["not_qualified"]


class ChargerStatus(BaseModel):
    enabled: bool
    status: Literal["disabled", "waiting", "ready", "stale", "error"]
    message: str
    reason_code: str
    last_success_at: str | None
    history_status: Literal["disabled", "waiting", "ready", "error"]
    history_message: str | None
    last_history_at: str | None
    observation: ChargerObservation | None
    validation: Literal["not_qualified"]
    retention_limit: int
    history_months: int
    energy_history: ChargerEnergyStatus


class ChargerSessionPage(BaseModel):
    items: list[ChargerSession]
    total: int
    limit: int
    offset: int


class ChargerPricingInputs(BaseModel):
    started_at: str
    ended_at: str
    kwh: float


class ChargerPricingEvent(BaseModel):
    id: str
    kind: Literal["priced", "baseline", "invalidated"]
    recorded_at: str
    session: ChargerPricingInputs
    quote: ChargeQuote
    corrected_session: ChargerPricingInputs | None


class ChargerPricingHistoryPage(BaseModel):
    session_id: str
    items: list[ChargerPricingEvent]
    total: int
    limit: int
    offset: int
    retention_limit: int


class Charge(BaseModel):
    id: str
    started_at: str
    kwh: float
    cost_eur: float
    source: Literal["manual", "demo"]
    tariff_eur_kwh: float | None
    ended_at: str | None
    pricing: PricingSnapshot | TemporalPricingSnapshot | None
    pricing_method: Literal["manual", "pvpc"] | None


class Totals(BaseModel):
    kwh: float
    cost_eur: float
    distance_km: float
    consumption_kwh_100km: float


class Sources(BaseModel):
    manual: Totals
    demo: Totals


class Energy(BaseModel):
    sessions: list[Charge]
    totals: Totals
    totals_by_source: Sources


class Journey(BaseModel):
    id: str
    started_at: str
    from_label: str
    to_label: str
    distance_km: float
    duration_min: int
    energy_kwh: float


class Maintenance(BaseModel):
    id: str
    title: str
    due_date: str
    completed: bool


class Event(BaseModel):
    id: str
    created_at: str
    kind: str
    message: str


class Protection(BaseModel):
    enabled: bool
    events: list[Event]
    observed_count: int = Field(ge=0, le=1000)


class ObservedEvent(BaseModel):
    id: str
    field: Literal["locked", "charging"]
    source: str
    before: bool
    after: bool
    previous_observed_at: str
    observed_at: str
    previous_received_at: str
    received_at: str
    previous_snapshot_id: str
    snapshot_id: str


class ObservedEventPage(BaseModel):
    items: list[ObservedEvent]
    total: int
    limit: int
    offset: int


class Command(BaseModel):
    id: str
    label: str
    enabled: bool
    reason: str


class CommandResult(BaseModel):
    id: str
    command: str
    status: str
    created_at: str
    message: str


class Connection(BaseModel):
    status: str
    reason: str
    freshness: str | None = None
    last_contact_at: str | None = None
    last_observed_at: str | None = None
    commands_enabled: bool = False
    capabilities: dict[str, Any] | None = None
    sdk: dict[str, Any] | None = None


class TelemetryValues(BaseModel):
    battery_pct: float | None
    range_km: float | None
    odometer_km: float | None
    interior_temperature_c: float | None = None
    battery_min_temperature_c: float | None = None
    front_left_pressure_kpa: float | None = None
    front_right_pressure_kpa: float | None = None
    rear_left_pressure_kpa: float | None = None
    rear_right_pressure_kpa: float | None = None
    vehicle_security_code: int | None = Field(default=None, ge=0, le=255, strict=True)
    sentry_code: int | None = Field(default=None, ge=0, le=255, strict=True)
    locked: bool | None
    climate_on: bool | None
    charging: bool | None


class TelemetrySnapshot(BaseModel):
    id: str
    source: str
    observed_at: str | None
    provider_received_at: str | None
    server_received_at: str
    vehicle: TelemetryValues
    field_sources: dict[str, str | None]


class TemporalQuality(BaseModel):
    freshness: Literal["unknown"]
    temporal_status: Literal["fresh", "stale", "unknown", "unavailable"]
    age_seconds: float | None
    observed_at: str | None
    reason_code: str


class TelemetryResponse(BaseModel):
    latest: TelemetrySnapshot | None
    quality: TemporalQuality
    collector: dict[str, Any]


class TelemetryPage(BaseModel):
    items: list[TelemetrySnapshot]
    total: int
    limit: int
    offset: int


class MqttStatus(BaseModel):
    enabled: bool
    status: Literal["disabled", "connecting", "connected", "error"]
    data_status: Literal["no_data", "demo", "unavailable", "published"]
    fields: list[Literal[
        "battery_pct", "range_km", "charging", "interior_temperature_c",
        "battery_min_temperature_c", "front_left_pressure_kpa", "front_right_pressure_kpa",
        "rear_left_pressure_kpa", "rear_right_pressure_kpa",
    ]]
    max_age_seconds: int
    observed_at: str | None
    last_published_at: str | None
    reason_code: Literal["disabled", "connecting", "waiting_observation", "demo_excluded",
                         "observation_unavailable", "published", "connection_failed", "publish_failed"]


class AbrpStatus(BaseModel):
    enabled: bool
    consent: bool
    status: Literal["disabled", "paused", "waiting", "sharing", "shared", "error"]
    reason_code: Literal["disabled", "consent_required", "demo_excluded", "waiting_observation",
                         "observation_invalid", "observation_stale", "already_attempted",
                         "sharing", "shared", "send_failed"]
    fields: list[Literal["battery_pct", "observed_at"]]
    max_age_seconds: int
    last_shared_at: str | None
    validation: Literal["not_qualified"]


class IntegrationsStatus(BaseModel):
    mqtt: MqttStatus
    abrp: AbrpStatus


class SetupStatus(BaseModel):
    """Public shape of server activation, never proof of physical qualification."""

    evidence_scope: Literal["configuration_only"]
    auth_mode: Literal["local", "oidc"]
    vehicle_reader_enabled: bool
    charger_enabled: bool
    interval_energy_enabled: bool
    native_app_enabled: bool
    native_widgets_enabled: bool
    web_push_enabled: bool
    home_assistant_enabled: bool
    abrp_enabled: bool
    navigation_enabled: bool
    physical_commands_enabled: Literal[False]


class StateResponse(BaseModel):
    app: AppInfo
    mode: Literal["demo", "unconfigured", "telemetry_read_only"]
    updated_at: str
    vehicle: Vehicle | None
    settings: Settings
    energy: Energy
    journeys: list[Journey]
    maintenance: list[Maintenance]
    protection: Protection
    commands: list[Command]
    command_history: list[CommandResult]
    connection: Connection
    telemetry: TelemetryResponse | None = None
    integrations: IntegrationsStatus
    setup: SetupStatus


class ChargePage(BaseModel):
    items: list[Charge]
    limit: int
    offset: int
    total: int


class CommandHistory(BaseModel):
    items: list[CommandResult]


class WidgetProfile(BaseModel):
    id: str
    name: str
    fields: list[Literal["battery_pct", "range_km", "charging", "locked", "energy_kwh", "energy_cost_eur"]]
    appearance: Literal["system", "light", "dark"]
    privacy_mode: Literal["masked", "visible"]
    max_age_minutes: int
    revision: int
    created_at: str
    updated_at: str


class WidgetProfileList(BaseModel):
    items: list[WidgetProfile]


class WidgetProfileResult(BaseModel):
    ok: bool
    profile: WidgetProfile


class NativeCapabilities(BaseModel):
    enabled: bool
    reason_code: Literal["configured", "not_configured"]
    pairing_ttl_seconds: Literal[300] = 300
    grant_ttl_days: Literal[30] = 30
    max_devices_per_profile: Literal[5] = 5


class WidgetPairingResult(BaseModel):
    pairing_code: str
    expires_at: str


class WidgetGrant(BaseModel):
    id: str
    profile_id: str
    device_name: str
    created_at: str
    expires_at: str
    last_seen_at: str | None = None


class WidgetGrantList(BaseModel):
    items: list[WidgetGrant]


class WidgetGrantResult(BaseModel):
    grant: WidgetGrant
    widget_token: str


class WidgetSnapshotProfile(BaseModel):
    id: str
    name: str
    revision: int
    fields: list[Literal["battery_pct", "range_km", "charging", "locked", "energy_kwh", "energy_cost_eur"]]
    appearance: Literal["system", "light", "dark"]
    privacy_mode: Literal["masked", "visible"]
    max_age_minutes: int


class WidgetEnergyWindow(BaseModel):
    started_at: str
    ended_at: str


class WidgetSnapshot(BaseModel):
    schema_version: Literal[2]
    profile: WidgetSnapshotProfile
    kind: Literal["vehicle", "energy"]
    energy_window: WidgetEnergyWindow | None
    source: Literal["demo", "telemetry", "unavailable", "manual_energy"]
    generated_at: str
    server_received_at: str | None
    observed_at: str | None
    physical_freshness: Literal["unknown"]
    temporal_status: Literal["fresh", "stale", "unknown", "unavailable"]
    reason_code: str
    valid_until: str | None
    values: dict[Literal["battery_pct", "range_km", "charging", "locked", "energy_kwh", "energy_cost_eur"], float | bool | None]


class ChargeAggregate(BaseModel):
    sessions: int
    kwh: float
    cost_eur: float
    effective_tariff_eur_kwh: float | None


class EnergyMonth(BaseModel):
    month: str
    manual: ChargeAggregate
    demo: ChargeAggregate


class ObservationCoverage(BaseModel):
    sample_count: int
    observed_count: int
    missing_observation_count: int
    future_timestamp_count: int
    outside_window_count: int
    non_monotonic_count: int
    duplicate_time_count: int
    gap_count: int
    max_gap_seconds: float | None
    covered_seconds: float
    coverage_percent: float | None
    first_observed_at: str | None
    last_observed_at: str | None
    odometer_delta_km: float | None
    eligible_interval_count: int
    retention_limit: int
    validation: Literal["not_qualified"]


class EnergyEstimates(BaseModel):
    consumption_kwh_100km: None = None
    reason_code: Literal["energy_not_observed"] = "energy_not_observed"


class EnergyAnalytics(BaseModel):
    window_start: str
    window_end: str
    bucket_timezone: Literal["UTC"]
    manual: ChargeAggregate
    demo: ChargeAggregate
    months: list[EnergyMonth]
    observations: ObservationCoverage
    estimates: EnergyEstimates
