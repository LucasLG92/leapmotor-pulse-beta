import blue from '../assets/b10-starry-night-blue.webp';
import purple from '../assets/b10-midnight-purple.webp';
import grey from '../assets/b10-tundra-grey.webp';
import black from '../assets/b10-metallic-black.webp';
import silver from '../assets/b10-stellar-silver.webp';
import white from '../assets/b10-lunar-white.webp';
import type { Settings } from './types';

export type VehicleColor = Settings['vehicle_color'];

// Names from the Spanish B10 range. Paint chips and artwork are illustrative.
export const vehicleColors = [
	{ id: 'starry_night_blue', label: 'Azul Noche Estrellada', swatch: '#203c61', image: blue },
	{ id: 'midnight_purple', label: 'Púrpura Medianoche', swatch: '#625b72', image: purple },
	{ id: 'tundra_grey', label: 'Gris Tundra', swatch: '#73777b', image: grey },
	{ id: 'metallic_black', label: 'Negro Metálico', swatch: '#24272b', image: black },
	{ id: 'stellar_silver', label: 'Plata Estelar', swatch: '#bdc3cc', image: silver },
	{ id: 'lunar_white', label: 'Blanco Lunar', swatch: '#f5f5f2', image: white }
] as const satisfies ReadonlyArray<{ id: NonNullable<VehicleColor>; label: string; swatch: string; image: string }>;

export function vehicleArtwork(color: VehicleColor) {
	return vehicleColors.find((option) => option.id === color) ?? vehicleColors[4];
}
