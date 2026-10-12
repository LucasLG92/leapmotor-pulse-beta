import test from 'node:test';
import assert from 'node:assert/strict';
import { internalAppPath, preparationSection } from '../src/lib/app-links.ts';

const origin = 'https://pulse.example.test';
const paths = ['/', '/energy', '/navigation', '/activity', '/protection', '/settings'];
const pathFor = (path) => internalAppPath(new URL(path, origin), origin, paths);

test('solo intercepta rutas de la app y el acceso conocido a preparación', () => {
	for (const path of paths) assert.equal(pathFor(path), path);
	assert.equal(pathFor('/settings#preparation'), '/settings#preparation');
	assert.equal(preparationSection('/settings#preparation'), 'preparation');
	assert.equal(preparationSection('/settings'), null);
});

test('conserva la navegación normal de enlaces externos, consultas y anclas desconocidas', () => {
	for (const path of ['/api/auth/login', '/settings?edit=1', '/settings#other', '/activity#preparation', 'https://other.example.test/settings#preparation', 'https://user:password@pulse.example.test/settings']) {
		assert.equal(pathFor(path), null);
		assert.equal(preparationSection(path), null);
	}
});
