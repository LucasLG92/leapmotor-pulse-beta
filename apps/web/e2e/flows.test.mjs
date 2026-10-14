import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium } from 'playwright';

let browser, server, directory, origin;
async function eventually(assertion, timeout = 5000) {
	const end = Date.now() + timeout;
	while (true) {
		try { return await assertion(); } catch (error) {
			if (Date.now() >= end) throw error;
			await new Promise(resolve => setTimeout(resolve, 25));
		}
	}
}
before(async () => {
	directory = await mkdtemp(join(tmpdir(), 'pulse-browser-tests-'));
	server = spawn(process.env.PULSE_E2E_PYTHON || 'python', ['e2e/server.py', directory], { stdio: ['ignore', 'pipe', 'inherit'] });
	origin = await new Promise((resolve, reject) => {
		const timer = setTimeout(() => reject(new Error('El servidor sintético no arrancó')), 10_000);
		let output = '';
		server.stdout.on('data', chunk => {
			output += chunk;
			if (!output.includes('\n')) return;
			clearTimeout(timer); resolve(JSON.parse(output.split('\n')[0]).origin);
		});
		server.once('error', error => { clearTimeout(timer); reject(error); });
		server.once('exit', code => { clearTimeout(timer); reject(new Error(`El servidor terminó: ${code}`)); });
	});
	await eventually(async () => assert.equal((await fetch(`${origin}/readyz`)).status, 200));
	browser = await chromium.launch();
});
after(async () => {
	await browser?.close();
	if (server && server.exitCode === null) {
		await new Promise(resolve => {
			const timer = setTimeout(() => server.kill('SIGKILL'), 5000);
			server.once('exit', () => { clearTimeout(timer); resolve(); });
			server.kill('SIGTERM');
		});
	}
	if (directory) await rm(directory, { recursive: true, force: true });
});
async function pageFor(t) {
	const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, serviceWorkers: 'block' });
	const page = await context.newPage(), errors = [];
	page.on('pageerror', error => errors.push(error.message));
	t.after(async () => { await page.unrouteAll({ behavior: 'ignoreErrors' }); await context.close(); assert.deepEqual(errors, [], 'errores de la interfaz'); });
	return page;
}
const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

test('las seis secciones conservan el reflujo móvil y los controles con texto al 200 %', { timeout: 60_000 }, async t => {
	const page = await pageFor(t);
	for (const width of [320, 390, 430]) {
		await page.setViewportSize({ width, height: 844 });
		for (const route of ['/', '/energy', '/activity', '/protection', '/navigation', '/settings']) {
			await page.goto(`${origin}${route}`);
			await page.locator('main[aria-busy="false"]').waitFor();
			await settle(page);
			assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), width, `${route}, ${width}px`);
		}
	}
	await page.setViewportSize({ width: 320, height: 844 });
	for (const theme of ['Claro', 'Oscuro']) {
		await page.goto(`${origin}/settings`);
		await page.getByRole('button', { name: 'Preferencias', exact: true }).click();
		await page.getByRole('button', { name: theme, exact: true }).click();
		for (const route of ['/', '/energy', '/activity', '/protection', '/navigation', '/settings']) {
			await page.goto(`${origin}${route}`);
			await page.locator('main[aria-busy="false"]').waitFor();
			await page.evaluate(() => document.documentElement.style.setProperty('font-size', '32px', 'important'));
			assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).fontSize), '32px');
			await settle(page);
			assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 320, `${route}, ${theme}, texto200%`);
			if (route === '/activity') {
				const buttons = await page.locator('.panel-actions button').evaluateAll(items => items.map(el => {
					const rect = el.getBoundingClientRect();
					return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, clipped: el.scrollWidth > el.clientWidth + 1 };
				}));
				assert.equal(buttons.length, 2);
				assert.ok(buttons.every(b => !b.clipped && b.left >= 0 && b.right <= 320));
				assert.ok(buttons[0].right <= buttons[1].left || buttons[1].right <= buttons[0].left || buttons[0].bottom <= buttons[1].top || buttons[1].bottom <= buttons[0].top, 'acciones sin solapamiento');
			}
			const selector = route === '/' ? '.control-actions' : route === '/settings' ? '.settings-section-switcher' : route === '/energy' ? '.energy-total-metrics' : null;
			if (selector) {
				const columns = await page.locator(selector).first().evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
				assert.equal(columns, 1, `${route}, rejilla adaptada al texto grande`);
			}
		}
	}
});

test('el campo inicial permanece accesible con poca altura y el descarte conserva el borrador al cancelar', { timeout: 30_000 }, async t => {
	const page = await pageFor(t);
	await page.setViewportSize({ width: 320, height: 450 });
	await page.goto(`${origin}/activity`);
	await page.evaluate(() => document.documentElement.style.setProperty('font-size', '32px', 'important'));
	await page.getByRole('button', { name: 'Añadir trayecto', exact: true }).click();
	await settle(page);
	const field = await page.locator('#journey-started').evaluate(el => {
		const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
		return { focused: document.activeElement === el, reached: document.elementFromPoint(x, y) === el };
	});
	assert.deepEqual(field, { focused: true, reached: true }, 'la navegación fija no tapa el campo enfocado');
	await page.locator('#journey-notes').fill('Borrador con texto grande');
	await page.locator('.compact-mobile-nav summary').click();
	await page.locator('.compact-mobile-nav').getByRole('link', { name: 'Energía', exact: true }).click();
	const dialog = page.getByRole('dialog');
	await dialog.waitFor();
	assert.equal(await dialog.evaluate(el => el.matches(':modal')), true);
	await page.keyboard.press('Escape');
	await dialog.waitFor({ state: 'hidden' });
	assert.equal(await page.locator('#journey-notes').inputValue(), 'Borrador con texto grande');
	assert.equal(new URL(page.url()).pathname, '/activity');
});
async function settings(page, name) {
	const session = await (await page.request.get(`${origin}/api/session`)).json();
	const response = await page.request.put(`${origin}/api/settings`, {
		headers: { 'X-Pulse-CSRF': session.csrf_token, Origin: origin }, data: { vehicle_name: name }
	});
	assert.equal(response.status(), 200);
}

test('Diario consulta cada pestaña al abrirla y conserva un borrador entre pestañas', { timeout: 30_000 }, async t => {
	const page = await pageFor(t), paths = [];
	page.on('request', request => { if (request.method() === 'GET') paths.push(new URL(request.url()).pathname); });
	await page.goto(`${origin}/activity`);
	await page.getByRole('button', { name: 'Añadir trayecto', exact: true }).click();
	await page.locator('#journey-notes').fill('Borrador sintético conservado');
	await settle(page);
	assert.equal(paths.filter(path => path === '/api/maintenance-plans').length, 0);
	assert.equal(paths.filter(path => path === '/api/commands/history').length, 0);
	await page.getByRole('button', { name: 'Mantenimiento', exact: true }).click();
	await eventually(() => assert.equal(paths.filter(path => path === '/api/maintenance-plans').length, 1));
	assert.equal(paths.filter(path => path === '/api/commands/history').length, 0);
	const previousJourneyReads = paths.filter(path => path === '/api/journeys').length;
	await settings(page, 'B10 actualizado desde otra vista');
	await page.getByRole('button', { name: 'Acciones', exact: true }).click();
	await eventually(() => assert.ok(paths.includes('/api/commands/history')));
	assert.equal(paths.filter(path => path === '/api/journeys').length, previousJourneyReads);
	await page.getByRole('button', { name: 'Trayectos', exact: true }).click();
	assert.equal(await page.locator('#journey-notes').inputValue(), 'Borrador sintético conservado');
});

test('Perfil difiere widgets y avisos; cambiar de pestaña conserva el nombre pendiente', { timeout: 30_000 }, async t => {
	const page = await pageFor(t), paths = [];
	page.on('request', request => { if (request.method() === 'GET') paths.push(new URL(request.url()).pathname); });
	await page.goto(`${origin}/settings`);
	await page.locator('#vehicle-name').fill('Nombre todavía sin guardar');
	await settle(page);
	for (const path of ['/api/widget-profiles', '/api/native-capabilities', '/api/push/capabilities']) assert.equal(paths.includes(path), false, path);
	await page.getByRole('button', { name: 'Conexiones', exact: true }).click(); await settle(page);
	assert.equal(paths.includes('/api/widget-profiles'), false);
	await page.getByRole('button', { name: 'Datos', exact: true }).click();
	await eventually(() => { assert.ok(paths.includes('/api/widget-profiles')); assert.ok(paths.includes('/api/native-capabilities')); });
	await page.getByRole('button', { name: 'Vehículo', exact: true }).click();
	assert.equal(await page.locator('#vehicle-name').inputValue(), 'Nombre todavía sin guardar');
	await page.getByRole('button', { name: 'Preferencias', exact: true }).click();
	await eventually(() => assert.ok(paths.includes('/api/push/capabilities')));
});

test('un guardado con respuesta perdida se reintenta con la misma identidad y no duplica el trayecto', { timeout: 30_000 }, async t => {
	const page = await pageFor(t), bodies = [];
	await page.goto(`${origin}/activity`);
	await page.getByRole('button', { name: 'Añadir trayecto', exact: true }).click();
	await page.locator('#journey-time-basis').selectOption('utc');
	const now = Date.now(), date = value => new Date(value).toISOString().slice(0, 16);
	await page.locator('#journey-started').fill(date(now - 7_200_000));
	await page.locator('#journey-ended').fill(date(now - 3_600_000));
	await page.locator('#journey-notes').fill('Viaje sintético con respuesta perdida');
	const previous = await (await page.request.get(`${origin}/api/journeys`)).json();
	await page.route('**/api/journeys', async route => {
		if (route.request().method() !== 'POST') return route.continue();
		bodies.push(route.request().postDataJSON());
		const response = await route.fetch();
		assert.equal(response.status(), 200);
		if (bodies.length === 1) await route.abort('failed');
		else await route.fulfill({ response });
	});
	await page.getByRole('button', { name: 'Guardar trayecto', exact: true }).click();
	await page.getByRole('button', { name: 'Reintentar el mismo guardado', exact: true }).click();
	await eventually(async () => {
		const current = await (await page.request.get(`${origin}/api/journeys`)).json();
		assert.equal(current.total, previous.total + 1); assert.equal(bodies.length, 2);
	});
	assert.equal(bodies[0].request_id, bodies[1].request_id);
	assert.deepEqual(bodies[0], bodies[1]);
	await page.locator('#journey-notes').waitFor({ state: 'hidden' });
});

test('los eventos durante una lectura lenta producen una reconciliación sin mostrar la respuesta antigua', { timeout: 30_000 }, async t => {
	const page = await pageFor(t);
	await page.addInitScript(() => {
		window.e2eRevision = null;
		const Original = window.EventSource;
		window.EventSource = class extends Original {
			constructor(...args) { super(...args); this.addEventListener('state', event => { window.e2eRevision = JSON.parse(event.data).revision; }); }
		};
	});
	await page.goto(`${origin}/settings`);
	await page.locator('#vehicle-name').waitFor(); await settle(page);
	let release; const held = new Promise(resolve => { release = resolve; });
	let reads = 0, firstFetched = false;
	await page.route('**/api/state', async route => {
		reads++;
		const response = await route.fetch();
		if (reads === 1) { firstFetched = true; await held; }
		await route.fulfill({ response });
	});
	t.after(() => release());
	await settings(page, 'Respuesta anterior');
	await eventually(() => assert.equal(firstFetched, true));
	const firstRevision = await page.evaluate(() => window.e2eRevision);
	await settings(page, 'Respuesta actual');
	await page.waitForFunction(revision => window.e2eRevision !== revision, firstRevision);
	// Release immediately after the newer event, including the debounce window.
	assert.equal(reads, 1, 'solo una consulta en curso');
	release();
	await eventually(async () => assert.equal(await page.locator('#vehicle-name').inputValue(), 'Respuesta actual'));
	assert.equal(reads, 2, 'una única reconciliación');
});
