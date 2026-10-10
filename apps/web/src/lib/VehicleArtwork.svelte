<script lang="ts">
	import { vehicleArtwork, type VehicleColor } from './vehicle-colors';
	let { color = null }: { color?: VehicleColor } = $props();
	let artwork = $derived(vehicleArtwork(color));
</script>

<figure class="vehicle-artwork">
	<div class="vehicle-stage">
		<img src={artwork.image} alt={`Ilustración del Leapmotor B10${color ? ` en ${artwork.label}` : ', sin color elegido'}`} width="627" height="280" decoding="async" />
	</div>
	<figcaption>Representación del B10 · {color ? artwork.label : 'Color sin elegir'}. El acabado es orientativo.</figcaption>
</figure>

<style>
	.vehicle-artwork { min-width: 0; margin: 0; }
	.vehicle-stage { overflow: hidden; padding: 12px 8px; border-radius: 16px; background: #eceef0; }
	img { display: block; width: 100%; height: auto; aspect-ratio: 627 / 280; object-fit: contain; }
	figcaption { margin-top: 10px; color: var(--muted); font-size: .75rem; line-height: 1.5; }
</style>
