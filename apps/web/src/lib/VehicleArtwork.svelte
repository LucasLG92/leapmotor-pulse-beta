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
	.vehicle-artwork { min-width: 0; min-height: 0; margin: 0; }
	.vehicle-frame {
		position: relative;
		isolation: isolate;
		display: grid;
		min-width: 0;
		min-height: 0;
		height: clamp(230px, 25vw, 340px);
		place-items: center;
		overflow: hidden;
		border: 1px solid color-mix(in srgb, var(--forest) 12%, var(--line));
		border-radius: clamp(22px, 3vw, 34px);
		background:
			radial-gradient(ellipse 72% 74% at 50% 88%, color-mix(in srgb, var(--forest) 9%, transparent), transparent 73%),
			linear-gradient(155deg, color-mix(in srgb, var(--surface-tint, var(--surface-soft)) 76%, var(--surface)), var(--surface));
		transition: border-color var(--motion-state, 220ms) ease;
	}
	.vehicle-frame::before {
		position: absolute;
		z-index: 0;
		inset: 58% 3% 19%;
		border-top: 1px solid color-mix(in srgb, var(--forest) 18%, var(--line));
		content: '';
		pointer-events: none;
	}
	.vehicle-frame::after {
		position: absolute;
		z-index: 0;
		bottom: 7%;
		left: 16%;
		width: 68%;
		height: 17%;
		border-radius: 50%;
		background: radial-gradient(ellipse, color-mix(in srgb, var(--forest) 12%, transparent), transparent 72%);
		content: '';
		filter: blur(7px);
		pointer-events: none;
	}
	img { position: relative; z-index: 1; display: block; min-width: 0; min-height: 0; max-width: 100%; max-height: 100%; width: 100%; height: 100%; object-fit: contain; object-position: 50% 60%; scale: 1.08; transform-origin: 50% 58%; animation: vehicle-art-arrive var(--motion-enter, 280ms) var(--ease-out, cubic-bezier(.22, 1, .36, 1)) both; transition: scale var(--motion-fast, 160ms) ease; }
	figcaption { margin-top: 9px; color: var(--muted); font-size: .75rem; line-height: 1.4; text-align: center; }
	@keyframes vehicle-art-arrive {
		from { opacity: .86; translate: 0 7px; }
		to { opacity: 1; translate: 0 0; }
	}
	@media (max-width: 700px) {
		.vehicle-frame { height: clamp(220px, 58vw, 300px); }
	}
	@media (prefers-reduced-motion: reduce) {
		img { animation: none; transition: none; scale: 1.08; }
		.vehicle-frame { transition: none; }
	}
</style>
