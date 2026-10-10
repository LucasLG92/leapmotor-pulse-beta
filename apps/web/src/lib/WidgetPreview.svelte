<script lang="ts">
	import { number, euros, relativeAge, dateTime } from './format';
	import type { EnergyAnalytics, PulseState, WidgetProfileInput } from './types';

	let { profile, pulseState, energy = null, energyLoading = false, energyError = '', online = true }: {
		profile: Pick<WidgetProfileInput, 'name' | 'fields' | 'appearance' | 'privacy_mode' | 'max_age_minutes'>;
		pulseState: PulseState;
		energy?: EnergyAnalytics | null;
		energyLoading?: boolean;
		energyError?: string;
		online?: boolean;
	} = $props();

	const fieldLabels = {
		battery_pct: 'Batería', range_km: 'Autonomía', charging: 'Carga', locked: 'Cierre',
		energy_kwh: 'Energía registrada', energy_cost_eur: 'Coste registrado'
	} as const;
	let isEnergy = $derived(profile.fields.some((field) => field === 'energy_kwh' || field === 'energy_cost_eur'));
	let hasLatestTelemetry = $derived(pulseState.mode === 'telemetry_read_only' && pulseState.telemetry?.latest != null);

	function observedAt(): string | null {
		if (isEnergy) return energy?.window_end ?? null;
		if (pulseState.mode === 'demo') return pulseState.vehicle?.observed_at ?? null;
		if (hasLatestTelemetry) return pulseState.telemetry?.quality.observed_at ?? null;
		return null;
	}

	function snapshotWithinAgeLimit(): boolean {
		if (!online) return false;
		if (isEnergy) {
			if (!energy || energy.manual.sessions <= 0 || energyLoading || energyError) return false;
		} else {
			if (!pulseState.vehicle) return false;
			if (pulseState.mode === 'telemetry_read_only' && (!hasLatestTelemetry || pulseState.telemetry?.quality.temporal_status === 'unavailable')) return false;
			if (pulseState.mode !== 'demo' && pulseState.mode !== 'telemetry_read_only') return false;
		}
		const timestamp = observedAt();
		if (!timestamp) return false;
		const ageMs = Date.now() - new Date(timestamp).getTime();
		return Number.isFinite(ageMs) && ageMs >= -60_000 && ageMs <= profile.max_age_minutes * 60_000;
	}

	function observationLabel(): string {
		if (!online) return 'Sin conexión';
		if (isEnergy && energyLoading) return 'Calculando…';
		if (isEnergy && energyError) return 'Cálculo no disponible';
		if (isEnergy && (!energy || energy.manual.sessions === 0)) return 'Sin registros en este periodo';
		if (!snapshotWithinAgeLimit()) return isEnergy ? 'Cálculo antiguo o no disponible' : 'Lectura antigua o no disponible';
		const timestamp = observedAt();
		return timestamp ? `${isEnergy ? 'Calculado' : 'Observado'} ${relativeAge(timestamp)}` : 'Sin fecha';
	}

	function fieldValue(field: keyof typeof fieldLabels): string {
		if (!snapshotWithinAgeLimit()) return '—';
		if (profile.privacy_mode === 'masked') return '•••';
		if (field === 'energy_kwh') return energy && Number.isFinite(energy.manual.kwh) ? `${number(energy.manual.kwh)} kWh` : '—';
		if (field === 'energy_cost_eur') return energy && Number.isFinite(energy.manual.cost_eur) ? euros(energy.manual.cost_eur) : '—';
		const vehicle = pulseState.vehicle;
		if (!vehicle) return '—';
		if (field === 'battery_pct' || field === 'range_km') {
			const value = number(vehicle[field]);
			return value === '—' ? value : `${value}${field === 'battery_pct' ? '%' : ' km'}`;
		}
		if (field === 'locked') return typeof vehicle.locked === 'boolean' ? (vehicle.locked ? 'Cerrado' : 'Abierto') : '—';
		return typeof vehicle.charging === 'boolean' ? (vehicle.charging ? 'Cargando' : 'En pausa') : '—';
	}

	function utcDay(value: string): string {
		const date = new Date(value);
		return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date) : '—';
	}
</script>

<section class="widget-preview" data-appearance={profile.appearance} aria-label="Vista previa del widget">
	<div class="widget-preview-topline">
		<span class="widget-brand"><span class="widget-mark" aria-hidden="true"></span> PULSE</span>
		<span class="widget-badge" class:widget-demo={!isEnergy && pulseState.mode === 'demo'}>{isEnergy ? 'REGISTROS' : pulseState.mode === 'demo' ? 'DEMO' : hasLatestTelemetry ? 'VEHÍCULO' : 'SIN LECTURA'}</span>
	</div>
	<div class="widget-heading">
		<strong>{profile.name || (isEnergy ? 'Mi energía' : 'Mi vehículo')}</strong>
		<span>{isEnergy ? 'Últimos 30 días · UTC' : pulseState.settings.vehicle_name || 'Mi vehículo'}</span>
	</div>
	{#if profile.fields.length}
		<div class="widget-values" class:widget-values-single={profile.fields.length === 1}>
			{#each profile.fields as field (field)}
				<div class="widget-value"><span>{fieldLabels[field]}</span><strong>{fieldValue(field)}</strong></div>
			{/each}
		</div>
	{:else}<p class="widget-empty">Elige al menos un dato para mostrarlo.</p>{/if}
	<div class="widget-preview-foot">
		<span>{profile.privacy_mode === 'masked' ? 'Valores ocultos' : 'Valores visibles'} · máx. {profile.max_age_minutes} min</span>
		<span title={observedAt() ? dateTime(observedAt()!) : undefined}>{observationLabel()}</span>
		{#if isEnergy && energy && online}<span>{utcDay(energy.window_start)} – {utcDay(energy.window_end)} · UTC</span>{/if}
	</div>
</section>

<style>
	.widget-preview { display: grid; gap: 18px; width: 100%; min-width: 0; padding: clamp(14px, 4vw, 20px); border: 1px solid #d6e0d6; border-radius: 18px; background: #f5f8f1; color: #18352b; box-shadow: 0 6px 18px rgb(24 53 43 / 6%); }
	.widget-preview[data-appearance='dark'] { border-color: #3c5044; background: #17251d; color: #f1f5ef; }
	.widget-preview-topline { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
	.widget-brand { display: inline-flex; align-items: center; gap: 8px; font-size: .75rem; font-weight: 750; letter-spacing: .1em; }
	.widget-mark { width: 12px; height: 12px; border: 2px solid currentColor; border-radius: 4px 4px 6px 4px; transform: rotate(45deg); }
	.widget-badge { padding: 4px 8px; border: 1px solid #7b8d80; border-radius: 999px; font-size: .75rem; font-weight: 650; }
	.widget-demo { border-color: #b66e24; background: #f4e9d9; color: #744312; }
	.widget-heading { display: grid; gap: 5px; min-width: 0; }
	.widget-heading strong { overflow-wrap: anywhere; font-size: 1.125rem; font-weight: 650; letter-spacing: -.03em; }
	.widget-heading span, .widget-value span, .widget-preview-foot, .widget-empty { color: #53665c; font-size: .75rem; line-height: 1.5; overflow-wrap: anywhere; }
	.widget-values { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
	.widget-values-single { grid-template-columns: 1fr; }
	.widget-value { display: grid; align-content: start; gap: 7px; min-width: 0; }
	.widget-value strong { overflow-wrap: anywhere; font-size: clamp(1.125rem, 4vw, 1.75rem); font-weight: 650; letter-spacing: -.035em; font-variant-numeric: tabular-nums; line-height: 1.2; }
	.widget-preview-foot { display: grid; gap: 3px; padding-top: 12px; border-top: 1px solid #d6e0d6; }
	.widget-empty { margin: 0; }
	.widget-preview[data-appearance='dark'] .widget-heading span, .widget-preview[data-appearance='dark'] .widget-value span, .widget-preview[data-appearance='dark'] .widget-preview-foot, .widget-preview[data-appearance='dark'] .widget-empty { color: #b7c7ba; }
	.widget-preview[data-appearance='dark'] .widget-preview-foot { border-color: #3c5044; }
	@media (prefers-color-scheme: dark) {
		.widget-preview[data-appearance='system'] { border-color: #3c5044; background: #17251d; color: #f1f5ef; }
		.widget-preview[data-appearance='system'] .widget-heading span, .widget-preview[data-appearance='system'] .widget-value span, .widget-preview[data-appearance='system'] .widget-preview-foot, .widget-preview[data-appearance='system'] .widget-empty { color: #b7c7ba; }
		.widget-preview[data-appearance='system'] .widget-preview-foot { border-color: #3c5044; }
	}
</style>
