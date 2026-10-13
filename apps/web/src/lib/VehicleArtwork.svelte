<script lang="ts">
	import { onMount } from 'svelte';
	import { isNativeApp } from './native';
	import { vehicleArtwork, type VehicleColor } from './vehicle-colors';
	let { color = null }: { color?: VehicleColor } = $props();
	let artwork = $derived(vehicleArtwork(color));
	let mounted = true;

	function warmArtwork() {
		if (!mounted || isNativeApp() || typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
		const id = artwork.id;
		void navigator.serviceWorker.ready.then((registration) => {
			if (mounted && !isNativeApp()) {
				registration.active?.postMessage({ type: 'PULSE_WARM_VEHICLE_ARTWORK', id });
			}
		}).catch(() => undefined);
	}

	onMount(() => {
		if (isNativeApp() || !('serviceWorker' in navigator)) {
			return () => { mounted = false; };
		}
		navigator.serviceWorker.addEventListener('controllerchange', warmArtwork);
		return () => {
			mounted = false;
			navigator.serviceWorker.removeEventListener('controllerchange', warmArtwork);
		};
	});
</script>

<figure class="vehicle-artwork" aria-label="Representación orientativa del Leapmotor B10">
	<div class="vehicle-frame">
		{#key artwork.id}
			<img
				src={artwork.image}
				alt={`Representación del Leapmotor B10 en ${color ? artwork.label : 'un color orientativo'}`}
				width="1120"
				height="630"
				decoding="async"
				fetchpriority="high"
				onload={warmArtwork}
			/>
		{/key}
	</div>
	<figcaption>B10 · {color ? artwork.label : 'color sin elegir'} · acabado orientativo</figcaption>
</figure>

<style>
 .vehicle-artwork { min-width: 0; margin: 0; }
 .vehicle-frame { display: flex; align-items: center; justify-content: center; min-width: 0; height: var(--vehicle-stage-height, clamp(180px, 25vw, 340px)); overflow: hidden; }
 img { display: block; width: 100%; height: 100%; min-width: 0; min-height: 0; max-height: 100%; object-fit: contain; animation: car-arrive 360ms var(--ease-out) both; }
 figcaption { margin-top: 0; text-align: center; color: var(--muted); font-size: .625rem; line-height: 1.4; }
 @keyframes car-arrive { from { opacity: .5; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
 @media (max-width: 600px) { .vehicle-frame { height: var(--vehicle-stage-height, clamp(140px, 45vw, 230px)); } }
 @media (prefers-reduced-motion: reduce) { img { animation: none; } }
</style>
