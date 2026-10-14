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
async function pageFor(t, viewport = { width: 1280, height: 800 }) {
	const context = await browser.newContext({ viewport, serviceWorkers: 'block' });
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

async function keyboardActivate(locator) {
	await locator.focus(); await locator.press('Enter');
}
async function activeIs(page, locator) {
	assert.equal(await locator.evaluate(element => element === document.activeElement), true, JSON.stringify(await page.evaluate(() => ({ hasFocus: document.hasFocus(), active: document.activeElement?.outerHTML.slice(0, 160) }))));
}

test('cerrar editores con teclado devuelve el foco y cancelar el descarte conserva el borrador', { timeout: 45_000 }, async t => {
	const cases = [
		{ path: '/navigation', panel: '.favorites-panel', opener: 'Añadir lugar', field: '#favorite-name', form: '.favorite-form' },
		{ path: '/activity', panel: '.manual-journeys', opener: 'Añadir trayecto', cancel: 'Descartar formulario', field: '#journey-notes', form: '.journey-form' },
		{ path: '/activity', tab: 'Mantenimiento', panel: '.maintenance-panel', opener: 'Añadir plan', field: '#maintenance-plan-title', form: '.maintenance-panel .maintenance-form' }
	];
	for (const viewport of [{ width: 390, height: 844 }, { width: 320, height: 450 }]) {
		const page = await pageFor(t, viewport);
		for (const item of cases) {
			await page.goto(`${origin}${item.path}`);
			if (item.tab) await page.getByRole('button', { name: item.tab, exact: true }).click();
			const panel = page.locator(item.panel), opener = panel.getByRole('button', { name: item.opener, exact: true });
			const form = page.locator(item.form), field = page.locator(item.field);
			await keyboardActivate(opener);
			await keyboardActivate(form.getByRole('button', { name: item.cancel || 'Cancelar', exact: true }));
			await form.waitFor({ state: 'hidden' });
			await eventually(() => activeIs(page, opener));
			assert.equal(await opener.evaluate(element => {
				const box = element.getBoundingClientRect();
				return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest('button') === element;
			}), true, `${item.opener}: el foco debe quedar visible`);
			await keyboardActivate(opener); await field.fill('Borrador sintético');
			await keyboardActivate(form.getByRole('button', { name: item.cancel || 'Cancelar', exact: true }));
			await page.locator('dialog[open]').waitFor(); await page.keyboard.press('Escape');
			assert.equal(await field.inputValue(), 'Borrador sintético');
			await keyboardActivate(form.getByRole('button', { name: item.cancel || 'Cancelar', exact: true }));
			await keyboardActivate(page.locator('dialog[open]').getByRole('button', { name: 'Descartar formulario', exact: true }));
			await form.waitFor({ state: 'hidden' });
			await eventually(() => activeIs(page, opener));
		}
	}
});

async function describedError(page, id) {
	const field = page.locator(`#${id}`);
	assert.equal(await field.getAttribute('aria-invalid'), 'true');
	const error = page.locator(`#${id}-error`), text = await error.textContent();
	assert.ok(text?.trim());
	assert.ok((await field.getAttribute('aria-describedby'))?.split(' ').includes(`${id}-error`));
	// Inspect the browser's computed accessibility description, beyond the markup.
	const label = await page.locator(`label[for="${id}"]`).textContent();
	const session = await page.context().newCDPSession(page);
	try {
		const { nodes } = await session.send('Accessibility.getFullAXTree');
		assert.ok(nodes.some(node => node.name?.value === label.trim() && node.description?.value.includes(text.trim())), `${id}: error en la descripción accesible`);
	} finally { await session.detach(); }
}

test('los errores de favoritos, mantenimiento y simulación forman parte de la descripción accesible', { timeout: 30_000 }, async t => {
	const page = await pageFor(t, { width: 390, height: 844 });
	await page.goto(`${origin}/navigation`);
	await page.getByRole('button', { name: 'Añadir lugar', exact: true }).click();
	await keyboardActivate(page.getByRole('button', { name: 'Guardar favorito', exact: true }));
	for (const id of ['favorite-name', 'favorite-latitude', 'favorite-longitude']) await describedError(page, id);
	await page.goto(`${origin}/activity`);
	await page.getByRole('button', { name: 'Mantenimiento', exact: true }).click();
	await page.getByRole('button', { name: 'Añadir plan', exact: true }).click();
	await page.locator('#maintenance-plan-intervalMonths').fill('0');
	await page.locator('#maintenance-plan-intervalKm').fill('0');
	await keyboardActivate(page.getByRole('button', { name: 'Crear plan', exact: true }));
	for (const id of ['maintenance-plan-title', 'maintenance-plan-intervalMonths', 'maintenance-plan-intervalKm']) await describedError(page, id);
	await page.locator('.maintenance-panel .maintenance-form').getByRole('button', { name: 'Cancelar', exact: true }).click();
	await page.locator('dialog[open]').getByRole('button', { name: 'Descartar formulario', exact: true }).click();
	await page.goto(`${origin}/energy`);
	await page.getByRole('button', { name: 'Planificar', exact: true }).click();
	await page.getByRole('button', { name: 'Abrir simulador', exact: true }).click();
	await keyboardActivate(page.getByRole('button', { name: 'Calcular escenario', exact: true }));
	for (const id of ['plan-energy', 'plan-power', 'plan-efficiency', 'plan-earliest', 'plan-deadline', 'tariff-0-start', 'tariff-0-end', 'tariff-0-price']) await describedError(page, id);
});

test('guardar devuelve el foco a un control estable aunque el refresco de la lista llegue tarde', { timeout: 40_000 }, async t => {
	for (const kind of ['favorite', 'maintenance', 'journey']) {
		const page = await pageFor(t, { width: 390, height: 844 });
		await page.bringToFront();
		const path = kind === 'favorite' ? '/navigation' : '/activity';
		await page.goto(`${origin}${path}`);
		if (kind === 'maintenance') await page.getByRole('button', { name: 'Mantenimiento', exact: true }).click();
		const name = `Prueba de foco ${kind}`;
		const endpoint = kind === 'favorite' ? '/api/navigation/favorites' : kind === 'maintenance' ? '/api/maintenance-plans' : '/api/journeys';
		const formSelector = kind === 'favorite' ? '.favorite-form' : kind === 'maintenance' ? '.maintenance-panel .maintenance-form' : '.journey-form';
		const stableId = kind === 'favorite' ? '#favorite-add' : kind === 'maintenance' ? '#maintenance-plan-add' : '#manual-journey-add';
		const emptyName = kind === 'favorite' ? 'Añadir el primero' : 'Crear primer plan';
		const emptyOpener = page.getByRole('button', { name: emptyName, exact: true });
		await keyboardActivate(kind !== 'journey' && await emptyOpener.isVisible() ? emptyOpener : page.locator(stableId));
		if (kind === 'favorite') {
			await page.locator('#favorite-name').fill(name);
			await page.locator('#favorite-latitude').fill('40'); await page.locator('#favorite-longitude').fill('-3');
		} else if (kind === 'maintenance') {
			await page.locator('#maintenance-plan-title').fill(name); await page.locator('#maintenance-plan-intervalMonths').fill('12');
		} else {
			const now = Date.now(), date = value => new Date(value).toISOString().slice(0, 16);
			await page.locator('#journey-time-basis').selectOption('utc');
			await page.locator('#journey-started').fill(date(now - 7_200_000)); await page.locator('#journey-ended').fill(date(now - 3_600_000));
			await page.locator('#journey-from').fill(name);
		}
		let release, wrote = false, reading = false;
		const held = new Promise(resolve => { release = resolve; });
		t.after(() => release());
		await page.route(`**${endpoint}*`, async route => {
			if (route.request().method() === 'POST') { wrote = true; await route.continue(); }
			else if (wrote && route.request().method() === 'GET') {
				const response = await route.fetch(); reading = true; await held; await route.fulfill({ response });
			} else await route.continue();
		});
		const form = page.locator(formSelector);
		await keyboardActivate(form.locator('button[type="submit"]'));
		await form.waitFor({ state: 'hidden' });
		await eventually(() => assert.equal(reading, true));
		await eventually(() => activeIs(page, page.locator(stableId)));
		release();
		await page.getByText(name, { exact: false }).first().waitFor();
		await settle(page);
		await activeIs(page, page.locator(stableId));
	}
});

test('el recorrido de teclado deja visible el foco delante de la navegación móvil', { timeout: 45_000 }, async t => {
	for (const viewport of [{ width: 390, height: 844 }, { width: 320, height: 450 }]) {
		const page = await pageFor(t, viewport); await page.bringToFront();
		for (const path of ['/', '/energy', '/activity', '/protection', '/navigation', '/settings']) {
			await page.goto(`${origin}${path}`); await page.locator('main[aria-busy="false"]').waitFor();
			for (let step = 0; step < 24; step++) {
				await page.keyboard.press('Tab'); await settle(page);
				const focus = await page.evaluate(() => {
					const element = document.activeElement;
					if (!(element instanceof HTMLElement) || !element.closest('.page-content') || element.id === 'pulse-content') return null;
					const box = element.getBoundingClientRect(), bar = document.querySelector('.mobile-navigation-shell').getBoundingClientRect();
					return { text: element.textContent?.trim().slice(0, 60) || element.id, top: box.top, bottom: box.bottom, height: box.height, barTop: bar.top };
				});
				if (focus) {
					assert.ok(focus.top >= 0 && focus.bottom <= focus.barTop, `${path} ${viewport.width}×${viewport.height}: ${JSON.stringify(focus)}`);
				}
			}
		}
	}
});


test('las fechas de los trayectos guardados se leen en líneas normales con texto grande', { timeout: 15_000 }, async t => {
	const page = await pageFor(t);
	await page.goto(`${origin}/activity`);
	await page.locator('.journey-details summary').first().waitFor();
	for (const width of [320, 390, 430]) {
		await page.setViewportSize({ width, height: 844 });
		for (const size of ['16px', '32px']) {
			await page.evaluate(size => document.documentElement.style.setProperty('font-size', size, 'important'), size);
			await settle(page);
			const metrics = await page.locator('.journey-details summary').first().evaluate(summary => {
				const date = summary.querySelector('.journey-date-source'), time = date.querySelector('time');
				const line = parseFloat(getComputedStyle(time).lineHeight) || parseFloat(getComputedStyle(time).fontSize) * 1.5;
				return { available: summary.getBoundingClientRect().width, date: date.getBoundingClientRect().width, height: time.getBoundingClientRect().height, line };
			});
			assert.ok(metrics.date >= metrics.available - 2, JSON.stringify(metrics));
			assert.ok(metrics.height <= metrics.line * 4, JSON.stringify(metrics));
		}
	}
});

test('una respuesta tardía de guardado respeta el foco que el usuario movió fuera del editor', { timeout: 15_000 }, async t => {
	const page = await pageFor(t, { width: 390, height: 844 });
	await page.goto(`${origin}/navigation`);
	await page.locator('#favorite-add').click();
	await page.locator('#favorite-name').fill('Respuesta tardía sintética');
	await page.locator('#favorite-latitude').fill('40'); await page.locator('#favorite-longitude').fill('-3');
	let release, received = false;
	const held = new Promise(resolve => { release = resolve; }); t.after(() => release());
	await page.route('**/api/navigation/favorites*', async route => {
		if (route.request().method() !== 'POST') { await route.continue(); return; }
		const response = await route.fetch(); received = true; await held; await route.fulfill({ response });
	});
	await keyboardActivate(page.getByRole('button', { name: 'Guardar favorito', exact: true }));
	await eventually(async () => assert.equal(received, true, await page.locator('.favorite-form').innerText()));
	const external = page.getByRole('link', { name: 'Ver historial de estados', exact: true });
	await external.focus(); release();
	await page.locator('.favorite-form').waitFor({ state: 'hidden' });
	await page.getByText('Respuesta tardía sintética', { exact: true }).waitFor();
	await settle(page); await activeIs(page, external);
});
