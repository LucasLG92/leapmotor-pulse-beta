<script lang="ts">
	import { vehicleArtwork, type VehicleColor } from './vehicle-colors';
	let { color = null }: { color?: VehicleColor } = $props();
	let artwork = $derived(vehicleArtwork(color));
</script>

<figure class="vehicle-artwork" aria-label="Representación orientativa del Leapmotor B10">
	<div class="vehicle-frame">
		<img
			src={artwork.image}
			alt={`Representación del Leapmotor B10 en ${color ? artwork.label : 'un color orientativo'}`}
			width="1120"
			height="630"
			decoding="async"
			fetchpriority="high"
		/>
	</div>
	<figcaption>B10 · {color ? artwork.label : 'color sin elegir'} · acabado orientativo</figcaption>
</figure>

<style>
	.vehicle-artwork { min-width: 0; min-height: 0; margin: 0; }
	.vehicle-frame { display: grid; min-width: 0; min-height: 0; height: 260px; place-items: center; }
	img { display: block; min-width: 0; min-height: 0; max-width: 100%; max-height: 100%; width: 100%; height: 100%; object-fit: contain; object-position: 50% 60%; }
	figcaption { margin-top: 2px; color: var(--muted); font-size: .75rem; line-height: 1.35; text-align: center; }
	@media (max-width: 700px) {
		.vehicle-frame { height: 180px; }
	}
</style>
