import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
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
	const syntheticEnvironment = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith('PULSE_')));
	server = spawn(process.env.PULSE_E2E_PYTHON || 'python', ['e2e/server.py', directory], { env: syntheticEnvironment, stdio: ['ignore', 'pipe', 'inherit'] });
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

async function openBackupPanel(page, path = '/settings') {
	await page.goto(`${origin}${path}`);
	await page.getByRole('button', { name: 'Datos', exact: true }).click();
	await page.getByRole('button', { name: 'Exportar JSON', exact: true }).waitFor();
}
async function selectSyntheticBackup(page, data) {
	const chooser = page.waitForEvent('filechooser');
	await keyboardActivate(page.getByRole('button', { name: 'Elegir archivo', exact: true }));
	await (await chooser).setFiles({ name: 'copia-sintetica.json', mimeType: 'application/json', buffer: Buffer.from(typeof data === 'string' ? data : JSON.stringify(data)) });
}

test('exportar recibe una copia JSON válida del servidor sintético sin perder el contexto de Datos', { timeout: 20_000 }, async t => {
	const page = await pageFor(t, { width: 390, height: 844 });
	await settings(page, 'Copia de prueba de interfaz'); await openBackupPanel(page);
	const downloadEvent = page.waitForEvent('download');
	await keyboardActivate(page.getByRole('button', { name: 'Exportar JSON', exact: true }));
	const download = await downloadEvent;
	assert.equal(download.suggestedFilename(), 'leapmotor-pulse-backup.json');
	assert.equal(await download.failure(), null);
	const data = JSON.parse(await readFile(await download.path(), 'utf8'));
	assert.equal(data.schema_version, 17); assert.equal(data.settings.vehicle_name, 'Copia de prueba de interfaz');
	await eventually(async () => assert.equal(await page.getByRole('button', { name: 'Exportar JSON', exact: true }).isEnabled(), true));
	assert.equal(await page.getByRole('button', { name: 'Datos', exact: true }).getAttribute('aria-pressed'), 'true');
	await activeIs(page, page.getByRole('button', { name: 'Exportar JSON', exact: true }));
});

test('cancelar una restauración conserva el borrador y no envía el archivo al servidor', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await page.goto(`${origin}/settings`);
	const backup = await (await page.request.get(`${origin}/api/export`)).json(); let imports = 0;
	page.on('request', request => { if (new URL(request.url()).pathname === '/api/import') imports++; });
	await page.locator('#vehicle-name').fill('Borrador anterior a la copia');
	await page.getByRole('button', { name: 'Datos', exact: true }).click();
	await selectSyntheticBackup(page, backup);
	await page.getByRole('dialog').waitFor(); await page.keyboard.press('Escape');
	await page.getByRole('dialog').waitFor({ state: 'hidden' });
	await eventually(() => activeIs(page, page.getByRole('button', { name: 'Elegir archivo', exact: true })));
	assert.equal(imports, 0); assert.equal(await page.locator('#backup-file').inputValue(), '');
	await page.getByRole('button', { name: 'Vehículo', exact: true }).click();
	assert.equal(await page.locator('#vehicle-name').inputValue(), 'Borrador anterior a la copia');
});

test('restaurar concilia los datos y conserva un destino de foco visible en Datos', { timeout: 20_000 }, async t => {
	for (const { path, viewport, fontSize } of [{ path: '/settings', viewport: { width: 390, height: 844 }, fontSize: 16 }, { path: '/settings#preparation', viewport: { width: 320, height: 450 }, fontSize: 32 }]) {
	const page = await pageFor(t, viewport);
	await settings(page, 'Nombre de la copia restaurada');
	const backup = await (await page.request.get(`${origin}/api/export`)).json();
	await settings(page, 'Nombre antes de restaurar'); await openBackupPanel(page, path);
	await page.evaluate(size => document.documentElement.style.fontSize = `${size}px`, fontSize);
	await page.getByRole('button', { name: 'Vehículo', exact: true }).click();
	await page.locator('#vehicle-name').fill('Borrador que se descartará al confirmar');
	await page.getByRole('button', { name: 'Datos', exact: true }).click();
	await selectSyntheticBackup(page, backup);
	await page.getByRole('dialog').getByRole('button', { name: 'Restaurar datos', exact: true }).click();
	await eventually(async () => assert.equal(await page.locator('#vehicle-name').inputValue(), 'Nombre de la copia restaurada'));
	await settle(page);
	t.diagnostic(JSON.stringify(await page.evaluate(() => ({ active: document.activeElement?.tagName, id: document.activeElement?.id, dataSelected: document.querySelector('button[aria-controls="settings-panel-data"]')?.getAttribute('aria-pressed') }))));
	assert.equal(await page.getByRole('button', { name: 'Datos', exact: true }).getAttribute('aria-pressed'), 'true');
	assert.equal(await page.locator('#backup-heading').evaluate(el => el === document.activeElement), true);
	assert.equal(await page.locator('#backup-heading').isVisible(), true);
	assert.equal(await page.locator('.settings-card[aria-labelledby="backup-heading"] [role="status"]').textContent(), 'La copia se ha restaurado.');
	assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), viewport.width);
	await eventually(async () => {
		const visibility = await page.locator('#backup-heading').evaluate(el => {
			const rect = el.getBoundingClientRect(), covering = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
			return { unobscured: covering === el, top: rect.top, bottom: rect.bottom, scrollY, covering: covering?.tagName, coveringClass: covering?.className };
		});
		assert.equal(visibility.unobscured, true, `el encabezado enfocado queda visible ante la navegación fija: ${JSON.stringify(visibility)}`);
	});
	const link = page.getByRole('link', { name: 'Energía', exact: true });
	if (!await link.isVisible()) await page.locator('.compact-mobile-nav summary').click();
	await link.click();
	await eventually(() => assert.equal(new URL(page.url()).pathname, '/energy'));
	assert.equal(await page.getByRole('dialog').count(), 0, 'el borrador descartado no deja una guarda antigua');
	}
});

test('restaurar respeta el foco que el usuario ha movido a la navegación mientras esperaba', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await settings(page, 'Copia con foco externo');
	const backup = await (await page.request.get(`${origin}/api/export`)).json(); await openBackupPanel(page);
	let release, fetched = false; const held = new Promise(resolve => { release = resolve; }); t.after(() => release());
	await page.route('**/api/import', async route => { const response = await route.fetch(); fetched = true; await held; await route.fulfill({ response }); });
	await selectSyntheticBackup(page, backup); await page.getByRole('dialog').getByRole('button', { name: 'Restaurar datos', exact: true }).click();
	await eventually(() => assert.equal(fetched, true));
	const link = page.getByRole('link', { name: 'Energía', exact: true }); await link.focus();
	release();
	await eventually(async () => assert.equal(await page.getByRole('button', { name: 'Elegir archivo', exact: true }).isEnabled(), true));
	await settle(page); await activeIs(page, link);
});

test('una restauración aplicada con lectura fallida permite recuperar la vista sin reenviar el archivo', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await settings(page, 'Copia para recuperar lectura');
	const backup = await (await page.request.get(`${origin}/api/export`)).json(); await openBackupPanel(page);
	let imports = 0, applied = false, readsReleased = false;
	await page.route('**/api/import', async route => { imports++; const response = await route.fetch(); applied = true; await route.fulfill({ response }); });
	await page.route('**/api/state', route => applied && !readsReleased ? route.fulfill({ status: 503, json: { detail: 'Lectura sintética no disponible' } }) : route.continue());
	await selectSyntheticBackup(page, backup); await page.getByRole('dialog').getByRole('button', { name: 'Restaurar datos', exact: true }).click();
	const retry = page.getByRole('button', { name: 'Reintentar conexión', exact: true }); await retry.waitFor();
	assert.match(await page.getByRole('alert').textContent(), /se restauró, pero no se pudo leer/);
	await eventually(() => activeIs(page, retry));
	readsReleased = true; await keyboardActivate(retry);
	await page.getByRole('button', { name: 'Elegir archivo', exact: true }).waitFor();
	assert.equal(imports, 1); assert.equal(await page.getByRole('button', { name: 'Datos', exact: true }).getAttribute('aria-pressed'), 'true');
	await eventually(() => activeIs(page, page.locator('#backup-heading')));
});

test('una respuesta perdida de restauración conserva el borrador y no repite el POST automáticamente', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await page.goto(`${origin}/settings`);
	const backup = await (await page.request.get(`${origin}/api/export`)).json();
	await page.locator('#vehicle-name').fill('Borrador pendiente del resultado'); await page.getByRole('button', { name: 'Datos', exact: true }).click();
	let imports = 0; await page.route('**/api/import', async route => { imports++; await route.fetch(); await route.abort('failed'); });
	await selectSyntheticBackup(page, backup); await page.getByRole('dialog').getByRole('button', { name: 'Restaurar datos', exact: true }).click();
	const error = page.locator('.settings-card[aria-labelledby="backup-heading"] .form-error');
	await eventually(async () => assert.match(await error.textContent(), /No se ha podido confirmar/));
	assert.equal(await page.getByRole('button', { name: 'Elegir archivo', exact: true }).isEnabled(), true);
	await page.getByRole('button', { name: 'Vehículo', exact: true }).click(); await settle(page);
	assert.equal(await page.locator('#vehicle-name').inputValue(), 'Borrador pendiente del resultado'); assert.equal(imports, 1);
});

test('un JSON inválido y una copia rechazada muestran errores persistentes sin sustituir los datos', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await settings(page, 'Datos que deben conservarse'); await openBackupPanel(page);
	let imports = 0; page.on('request', request => { if (new URL(request.url()).pathname === '/api/import') imports++; });
	await selectSyntheticBackup(page, '{json inválido');
	const error = page.locator('.settings-card[aria-labelledby="backup-heading"] .form-error');
	await eventually(async () => assert.match(await error.textContent(), /JSON válido/));
	assert.equal(imports, 0); assert.equal(await page.getByRole('dialog').count(), 0);
	await selectSyntheticBackup(page, { schema_version: 999 });
	await page.getByRole('dialog').getByRole('button', { name: 'Restaurar datos', exact: true }).click();
	await eventually(async () => assert.match(await error.textContent(), /No se ha podido confirmar/));
	assert.equal(imports, 1);
	assert.equal((await (await page.request.get(`${origin}/api/export`)).json()).settings.vehicle_name, 'Datos que deben conservarse');
	assert.equal(await page.getByRole('button', { name: 'Elegir archivo', exact: true }).isEnabled(), true);
	assert.equal(await page.locator('#backup-file').inputValue(), '');
});

test('perder la conexión conserva el borrador y permite guardar y descargar explícitamente al recuperar la red', { timeout: 30_000 }, async t => {
	const page = await pageFor(t, { width: 390, height: 844 });
	await settings(page, 'Estado sintético previo a perder conexión');
	await page.goto(`${origin}/settings`);
	await page.locator('#vehicle-name').fill('Borrador sintético durante la desconexión');
	let writes = 0;
	page.on('request', request => { if (request.method() === 'PUT' && new URL(request.url()).pathname === '/api/settings') writes++; });
	await page.context().setOffline(true);
	await page.waitForFunction(() => !navigator.onLine);
	await keyboardActivate(page.getByRole('button', { name: 'Guardar ajustes', exact: true }));
	await page.locator('#settings-general-error').waitFor();
	assert.match(await page.locator('#settings-general-error').textContent(), /Tus cambios siguen en el formulario/);
	assert.equal(await page.locator('#vehicle-name').inputValue(), 'Borrador sintético durante la desconexión');
	assert.equal((await (await fetch(`${origin}/api/state`)).json()).settings.vehicle_name, 'Estado sintético previo a perder conexión');
	const failedWrites = writes;
	await page.getByRole('button', { name: 'Datos', exact: true }).click();
	const exportButton = page.getByRole('button', { name: 'Exportar JSON', exact: true });
	await keyboardActivate(exportButton);
	const error = page.locator('.settings-card[aria-labelledby="backup-heading"] .form-error');
	await error.waitFor(); assert.match(await error.textContent(), /Comprueba la conexión/);
	await eventually(async () => assert.equal(await exportButton.isEnabled(), true));
	await activeIs(page, exportButton);
	await page.context().setOffline(false);
	await page.waitForFunction(() => navigator.onLine);
	await page.getByRole('button', { name: 'Vehículo', exact: true }).click();
	assert.equal(await page.locator('#vehicle-name').inputValue(), 'Borrador sintético durante la desconexión');
	assert.equal(writes, failedWrites, 'recuperar la red no repite el guardado');
	await keyboardActivate(page.getByRole('button', { name: 'Guardar ajustes', exact: true }));
	await eventually(async () => assert.equal((await (await fetch(`${origin}/api/state`)).json()).settings.vehicle_name, 'Borrador sintético durante la desconexión'));
	assert.equal(writes, failedWrites + 1);
	await page.getByRole('button', { name: 'Datos', exact: true }).click();
	const downloadEvent = page.waitForEvent('download');
	await keyboardActivate(exportButton);
	const download = await downloadEvent;
	assert.equal(await download.failure(), null);
	assert.equal(download.suggestedFilename(), 'leapmotor-pulse-backup.json');
	const data = JSON.parse(await readFile(await download.path(), 'utf8'));
	assert.equal(data.settings.vehicle_name, 'Borrador sintético durante la desconexión');
});

test('la recuperación automática del arranque repone solo el foco que se perdió con Reintentar', { timeout: 30_000 }, async t => {
	for (const focused of [true, false]) {
		const page = await pageFor(t, { width: 390, height: 844 });
		let first = true, held = false, release;
		const blocked = new Promise(resolve => { release = resolve; }); t.after(() => release());
		await page.route('**/api/state', async route => {
			if (first) { first = false; held = true; await blocked; }
			await route.continue();
		});
		await page.goto(`${origin}/settings`);
		await eventually(() => assert.equal(held, true));
		await page.context().setOffline(true); release();
		const retry = page.getByRole('button', { name: 'Reintentar conexión', exact: true }); await retry.waitFor();
		assert.match(await page.getByRole('alert').textContent(), /Pulse está sin conexión/);
		if (focused) await retry.focus();
		await page.context().setOffline(false);
		await page.locator('#vehicle-name').waitFor(); await settle(page);
		if (focused) {
			await activeIs(page, page.locator('#page-title'));
			assert.equal(await page.locator('#page-title').isVisible(), true);
		} else assert.equal(await page.evaluate(() => document.activeElement === document.body), true, 'sin foco perdido activo no debe enfocar otro control');
	}
});

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
			t.diagnostic(`${item.path} ${item.opener} ${viewport.width}×${viewport.height}`);
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
			await keyboardActivate(opener); await settle(page); await field.fill('Borrador sintético');
			assert.equal(await field.inputValue(), 'Borrador sintético');
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

test('el foco inicial diferido de favoritos respeta otro control que el usuario ya ha enfocado', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await page.goto(`${origin}/navigation`);
	await page.locator('#favorite-add').waitFor(); await settle(page);
	await page.evaluate(async () => {
		const original = window.requestAnimationFrame, callbacks = [];
		window.requestAnimationFrame = callback => { callbacks.push(callback); return 1_000_000 + callbacks.length; };
		try {
			const opener = document.getElementById('favorite-add'); opener.focus(); opener.click();
			for (let i = 0; i < 20 && !document.querySelector('.favorite-form'); i++) await Promise.resolve();
			const cancel = [...document.querySelectorAll('.favorite-form button')].find(button => button.textContent.trim() === 'Cancelar');
			if (!cancel) throw new Error('El formulario no se abrió');
			cancel.focus();
		} finally {
			window.requestAnimationFrame = original;
			for (const callback of callbacks) original(callback);
		}
	});
	await settle(page);
	await activeIs(page, page.locator('.favorite-form').getByRole('button', { name: 'Cancelar', exact: true }));
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
	await keyboardActivate(page.locator('.favorite-form').getByText('Introducir coordenadas', { exact: true }));
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
			await page.locator('.favorite-form').getByText('Introducir coordenadas', { exact: true }).click();
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
	await page.locator('.favorite-form').getByText('Introducir coordenadas', { exact: true }).click();
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


test('Manual y Demostración se leen completos en los resúmenes y meses de energía', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await page.goto(`${origin}/energy`);
	await page.locator('.analytics-panel').waitFor();
	for (const width of [320, 390, 430]) {
		await page.setViewportSize({ width, height: 844 });
		for (const size of ['16px', '32px']) {
			await page.evaluate(size => document.documentElement.style.setProperty('font-size', size, 'important'), size); await settle(page);
			const items = await page.locator('.analytics-source-heading h3, .month-origin-row > span').evaluateAll(elements => elements.map(element => {
				const range = document.createRange(); const text = [...element.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim()); range.selectNodeContents(text || element);
				return { text: element.textContent?.trim(), width: element.getBoundingClientRect().width, lines: range.getClientRects().length, overflow: element.scrollWidth > element.clientWidth + 1 };
			}));
			assert.ok(items.length >= 2);
			for (const item of items) { assert.equal(item.lines, 1, `${width}/${size} ${JSON.stringify(item)}`); assert.equal(item.overflow, false, JSON.stringify(item)); }
			assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
		}
	}
});

async function enableSyntheticPlaces(page) {
	await page.route('**/api/navigation/status', route => route.fulfill({ json: { enabled: true, source: 'OpenStreetMap', reason: 'ready', attribution: '© OpenStreetMap contributors', places_provider: 'photon_private', private_place_search: true } }));
}
const publicPlaceFixture = { id: 'synthetic-place', name: 'Museo público de prueba', latitude: 40.42, longitude: -3.69 };
function syntheticPlaces() { return { source: 'OpenStreetMap Photon', fetched_at: new Date().toISOString(), cached: false, places: [publicPlaceFixture] }; }

test('el formulario de favoritos no envía direcciones a un proveedor público o a un estado privado incoherente', { timeout: 20_000 }, async t => {
	const page = await pageFor(t);
	let searches = 0;
	await page.route('**/api/navigation/places', route => { searches++; return route.fulfill({ json: syntheticPlaces() }); });
	for (const extra of [{}, { places_provider: 'nominatim', private_place_search: false }, { places_provider: 'photon_private', private_place_search: false }]) {
		await page.route('**/api/navigation/status', route => route.fulfill({ json: { enabled: true, source: 'OpenStreetMap', reason: 'ready', attribution: '© OpenStreetMap contributors', ...extra } }));
		await page.goto(`${origin}/navigation`);
		await page.locator('#favorite-add').click();
		assert.equal(await page.locator('#favorite-query').isDisabled(), true);
		assert.match(await page.locator('#favorite-query-help').textContent(), /búsqueda privada aún no está disponible/);
		assert.equal(searches, 0);
		await keyboardActivate(page.locator('.favorite-form').getByRole('button', { name: 'Guardar favorito', exact: true }));
		await page.locator('.form-errors').waitFor();
		assert.equal(await page.locator('#favorite-latitude').isVisible(), true);
		assert.equal(await page.locator('#favorite-latitude').isEnabled(), true);
		assert.equal(await page.locator('.form-errors a[href="#favorite-query"]').count(), 0, 'el error debe apuntar a campos accesibles');
		await page.unroute('**/api/navigation/status');
	}
});

test('la búsqueda privada identifica su procedencia y rechaza una respuesta pública sin cambiar el borrador', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await enableSyntheticPlaces(page);
	await page.route('**/api/navigation/places', route => route.fulfill({ json: { ...syntheticPlaces(), source: 'OpenStreetMap Nominatim' } }));
	await page.goto(`${origin}/navigation`); await page.locator('#favorite-add').click();
	const form = page.locator('.favorite-form');
	assert.match(await page.locator('#favorite-query-help').textContent(), /búsqueda se realiza en tu servidor/);
	await page.locator('#favorite-name').fill('Alias de prueba');
	await page.locator('#favorite-query').fill('Dirección sintética');
	await form.getByRole('button', { name: 'Buscar lugar', exact: true }).click();
	await page.locator('#favorite-query-error').waitFor();
	assert.equal(await page.locator('#favorite-name').inputValue(), 'Alias de prueba');
	assert.equal(await page.locator('#favorite-latitude').inputValue(), '');
	assert.equal(await form.locator('.lookup-results').count(), 0);
});

test('buscar y elegir un lugar obtiene coordenadas sin guardarlo; escribir o confirmar composición no envía consultas', { timeout: 20_000 }, async t => {
	const page = await pageFor(t, { width: 390, height: 844 }); await enableSyntheticPlaces(page);
	let searches = [], writes = [];
	await page.route('**/api/navigation/places', async route => { searches.push(route.request().postDataJSON()); await route.fulfill({ json: syntheticPlaces() }); });
	await page.route('**/api/navigation/favorites*', async route => { if (route.request().method() === 'POST') writes.push(route.request().postDataJSON()); await route.continue(); });
	await page.goto(`${origin}/navigation`); await page.locator('#favorite-add').click();
	const form = page.locator('.favorite-form'), query = page.locator('#favorite-query');
	await eventually(async () => assert.equal(await query.isEnabled(), true));
	assert.equal(await page.locator('#favorite-latitude').isVisible(), false);
	await query.fill('Museo público'); await settle(page); assert.equal(searches.length, 0);
	await query.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true }); await settle(page); assert.equal(searches.length, 0);
	await query.press('Enter');
	await form.getByRole('button').filter({ hasText: publicPlaceFixture.name }).click();
	assert.equal(await page.locator('#favorite-name').inputValue(), publicPlaceFixture.name);
	assert.equal(await page.locator('#favorite-latitude').inputValue(), String(publicPlaceFixture.latitude));
	assert.equal(await page.locator('#favorite-longitude').inputValue(), String(publicPlaceFixture.longitude));
	assert.deepEqual(searches, [{ query: 'Museo público', consent: true }]); assert.equal(writes.length, 0);
	await keyboardActivate(form.getByRole('button', { name: 'Guardar favorito', exact: true }));
	await form.waitFor({ state: 'hidden' });
	assert.deepEqual(writes, [{ name: publicPlaceFixture.name, latitude: publicPlaceFixture.latitude, longitude: publicPlaceFixture.longitude }]);
	await eventually(() => activeIs(page, page.locator('#favorite-add')));
});

test('una búsqueda tardía no repone resultados al cambiar el texto y conserva el borrador al cancelar el cierre', { timeout: 20_000 }, async t => {
	const page = await pageFor(t); await enableSyntheticPlaces(page);
	let release, requested = false; const held = new Promise(resolve => { release = resolve; }); t.after(() => release());
	await page.route('**/api/navigation/places', async route => { requested = true; await held; try { await route.fulfill({ json: syntheticPlaces() }); } catch {} });
	await page.goto(`${origin}/navigation`); await page.locator('#favorite-add').click();
	const form = page.locator('.favorite-form'), query = page.locator('#favorite-query');
	await eventually(async () => assert.equal(await query.isEnabled(), true));
	await query.fill('Primera consulta'); await query.press('Enter'); await eventually(() => assert.equal(requested, true));
	await query.fill('Consulta nueva'); release(); await settle(page);
	assert.equal(await form.locator('.lookup-result').count(), 0);
	await keyboardActivate(form.getByRole('button', { name: 'Cancelar', exact: true }));
	await page.locator('dialog[open]').waitFor(); await page.keyboard.press('Escape');
	assert.equal(await query.inputValue(), 'Consulta nueva');
	assert.equal(await page.locator('#favorite-name').inputValue(), '');
});

test('guardar coordenadas manuales sigue funcionando después de plegar el bloque opcional', { timeout: 15_000 }, async t => {
	const page = await pageFor(t); await page.goto(`${origin}/navigation`); await page.locator('#favorite-add').click();
	const form = page.locator('.favorite-form'), summary = form.getByText('Introducir coordenadas', { exact: true });
	await summary.click(); await page.locator('#favorite-name').fill('Coordenadas plegadas de prueba');
	await page.locator('#favorite-latitude').fill('40'); await page.locator('#favorite-longitude').fill('-3');
	await summary.click(); assert.equal(await page.locator('#favorite-latitude').isVisible(), false);
	await keyboardActivate(form.getByRole('button', { name: 'Guardar favorito', exact: true }));
	await form.waitFor({ state: 'hidden' }); await page.getByText('Coordenadas plegadas de prueba', { exact: true }).waitFor();
});
