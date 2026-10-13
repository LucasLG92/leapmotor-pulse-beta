"""Authenticated manual planning tools; no vehicle or external-service actions."""
import asyncio
from typing import Literal

from fastapi import Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from pulse.domain import DomainError
from pulse.energy_planning import plan_charging, compare_manual_charges, EnergyPlanningError
from pulse.journey_documents import journey_csv, journey_print_html


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class MaintenancePlanFieldsPayload(StrictPayload):
    title: str = Field(min_length=1, max_length=120)
    interval_months: int | None = Field(default=None, ge=1, le=120)
    interval_km: int | None = Field(default=None, ge=1, le=200000)
    last_date: str | None = Field(default=None, max_length=10)
    last_odometer_km: int | None = Field(default=None, ge=0, le=2000000)
    notes: str = Field(default='', max_length=2000)


class MaintenancePlanPayload(MaintenancePlanFieldsPayload):
    request_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)


class MaintenancePlanEditPayload(MaintenancePlanFieldsPayload):
    revision: int = Field(ge=1)


class RevisionPayload(StrictPayload):
    revision: int = Field(ge=1)


class MaintenanceCompletionPayload(RevisionPayload):
    performed_date: str = Field(max_length=10)
    odometer_km: int | None = Field(default=None, ge=0, le=2000000)
    cost_eur: float | None = Field(default=None, ge=0, le=100000)


class FavoritePayload(StrictPayload):
    name: str = Field(min_length=1, max_length=160)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class PlanningTariff(StrictPayload):
    start_at: str = Field(max_length=64)
    end_at: str = Field(max_length=64)
    price_eur_kwh: float = Field(ge=-1, le=5)


class ChargingPlanPayload(StrictPayload):
    energy_kwh: float = Field(gt=0, le=300)
    power_kw: float = Field(gt=0, le=350)
    efficiency_pct: float = Field(ge=50, le=100)
    earliest_at: str = Field(max_length=64)
    deadline_at: str = Field(max_length=64)
    tariffs: list[PlanningTariff] = Field(max_length=336)


class MaintenanceHistoryResult(BaseModel):
    id: str
    performed_date: str
    odometer_km: int | None
    cost_eur: float | None
    recorded_at: str


class MaintenancePlanResult(BaseModel):
    id: str
    revision: int
    source: Literal['manual']
    title: str
    interval_months: int | None
    interval_km: int | None
    last_date: str | None
    last_odometer_km: int | None
    notes: str
    created_at: str
    updated_at: str
    due_date: str | None
    due_odometer_km: int | None
    status: Literal['due','upcoming','unknown']
    due_reasons: list[str]
    coverage: dict[str, Literal['due','upcoming','unknown','not_configured']]
    history: list[MaintenanceHistoryResult]


class MaintenancePlanPage(BaseModel):
    items: list[MaintenancePlanResult]
    total: int


class FavoriteResult(FavoritePayload):
    id: str
    created_at: str


class FavoritePage(BaseModel):
    items: list[FavoriteResult]


class ChargingPlanInterval(BaseModel):
    started_at: str
    ended_at: str
    tariff_ended_at: str
    duration_seconds: float
    price_eur_kwh: float
    grid_energy_kwh: float
    battery_energy_kwh: float
    estimated_cost_eur: float
    partial: bool


class ChargingPlanCoverage(BaseModel):
    window_seconds: float
    priced_seconds: float
    complete: bool


class ChargingPlanBaseline(BaseModel):
    intervals: list[ChargingPlanInterval]
    grid_energy_kwh: float
    estimated_cost_eur: float


class ChargingPlanResult(BaseModel):
    purpose: Literal['informational_estimate']
    requested_energy_kwh: float
    efficiency_pct: float
    coverage: ChargingPlanCoverage
    intervals: list[ChargingPlanInterval]
    grid_energy_kwh: float
    battery_energy_kwh: float
    estimated_cost_eur: float
    satisfied_energy_kwh: float
    unsatisfied_energy_kwh: float
    earliest_feasible: ChargingPlanBaseline
    estimated_savings_eur: float | None
    warnings: list[str]


class ChargeComparisonTotals(BaseModel):
    charge_count: int
    kwh: float | None
    cost_eur: float | None
    known_kwh_count: int
    known_cost_count: int
    paired_count: int
    paired_kwh: float | None
    paired_cost_eur: float | None
    effective_cost_eur_kwh: float | None
    missing_kwh_count: int
    missing_cost_count: int


class ChargeComparisonLocation(ChargeComparisonTotals):
    location: str | None


class ChargeComparisonMonth(ChargeComparisonTotals):
    month: str
    locations: list[ChargeComparisonLocation]


class ChargeComparisonResult(BaseModel):
    basis: Literal['manual_entries_only']
    days: int
    window_start: str
    window_end: str
    totals: ChargeComparisonTotals
    months: list[ChargeComparisonMonth]
    excluded_non_manual_count: int


def register_evolution(app, coordinator, owner_id):
    @app.get('/api/maintenance-plans', response_model=MaintenancePlanPage)
    async def maintenance_plans(request: Request, odometer_km: int | None = Query(default=None, ge=0, le=2000000), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)):
        return await coordinator.call('list_maintenance_plans', owner_id(request), odometer_km=odometer_km, limit=limit, offset=offset)

    @app.post('/api/maintenance-plans', response_model=MaintenancePlanResult)
    async def create_plan(request: Request, values: MaintenancePlanPayload):
        return await coordinator.call('create_maintenance_plan', owner_id(request), values.model_dump(exclude={'request_id'}), request_id=values.request_id, write=True)

    @app.put('/api/maintenance-plans/{plan_id}', response_model=MaintenancePlanResult)
    async def edit_plan(request: Request, plan_id: str, values: MaintenancePlanEditPayload):
        return await coordinator.call('update_maintenance_plan', owner_id(request), plan_id, values.revision, values.model_dump(exclude={'revision'}), write=True)

    @app.delete('/api/maintenance-plans/{plan_id}')
    async def delete_plan(request: Request, plan_id: str, values: RevisionPayload):
        return await coordinator.call('delete_maintenance_plan', owner_id(request), plan_id, values.revision, write=True)

    @app.post('/api/maintenance-plans/{plan_id}/complete', response_model=MaintenancePlanResult)
    async def complete_plan(request: Request, plan_id: str, values: MaintenanceCompletionPayload):
        return await coordinator.call('complete_maintenance_plan', owner_id(request), plan_id, values.revision, values.performed_date, values.odometer_km, values.cost_eur, write=True)

    @app.get('/api/navigation/favorites', response_model=FavoritePage)
    async def favorites(request: Request):
        return await coordinator.call('list_navigation_favorites', owner_id(request))

    @app.post('/api/navigation/favorites', response_model=FavoriteResult)
    async def save_favorite(request: Request, values: FavoritePayload):
        return await coordinator.call('create_navigation_favorite', owner_id(request), values.model_dump(), write=True)

    @app.delete('/api/navigation/favorites/{favorite_id}')
    async def remove_favorite(request: Request, favorite_id: str, values: StrictPayload):
        return await coordinator.call('delete_navigation_favorite', owner_id(request), favorite_id, write=True)

    @app.post('/api/energy/plan', response_model=ChargingPlanResult)
    async def charging_plan(request: Request, values: ChargingPlanPayload):
        owner_id(request)
        try:
            return plan_charging(values.model_dump())
        except EnergyPlanningError as exc:
            raise DomainError("Revisa energía, potencia, eficiencia y fechas con zona horaria; los precios deben cubrir intervalos de hasta una hora sin solaparse.") from None

    @app.get('/api/energy/comparison', response_model=ChargeComparisonResult)
    async def comparison(request: Request, days: str = Query(default="90", pattern=r"^(30|90|365)$")):
        owner_id(request)
        return await coordinator.call('manual_charge_comparison', int(days))

    @app.get('/api/journeys/document')
    async def journey_document(request: Request, days: str = Query(default="90", pattern=r"^(30|90|365)$"), format: Literal['csv','print'] = 'csv'):
        data = await coordinator.call('journey_document_data', owner_id(request), int(days))
        if format == 'csv':
            return Response(await asyncio.to_thread(journey_csv, data), media_type='text/csv; charset=utf-8', headers={'Content-Disposition':f'attachment; filename="pulse-viajes-{days}d.csv"'})
        return Response(await asyncio.to_thread(journey_print_html, data), media_type='text/html; charset=utf-8')
