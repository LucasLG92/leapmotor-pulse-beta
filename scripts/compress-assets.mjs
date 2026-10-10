/** Precompress only generic build text. No runtime dependency or private input. */
import { readdir, readFile, writeFile, unlink, lstat } from 'node:fs/promises';
import { resolve, join, extname } from 'node:path';
import { gzipSync, gunzipSync } from 'node:zlib';

const directory = resolve(process.argv[2] || 'dist');
const extensions = new Set(['.html', '.js', '.css', '.svg', '.webmanifest']);
let count = 0;
let originalBytes = 0;
let compressedBytes = 0;

async function compressDirectory(path) {
	for (const entry of await readdir(path, { withFileTypes: true })) {
		const file = join(path, entry.name);
		if (entry.isSymbolicLink()) throw new Error('Symlink is not a generic build asset.');
		if (entry.isDirectory()) {
			await compressDirectory(file);
			continue;
		}
		if (!entry.isFile() || !extensions.has(extname(file))) continue;
		const data = await readFile(file);
		const sidecar = file + '.gz';
		const encoded = gzipSync(data, { level: 9 });
		if (encoded.length >= data.length) {
			await unlink(sidecar).catch((error) => { if (error.code !== 'ENOENT') throw error; });
			continue;
		}
		if (!gunzipSync(encoded).equals(data)) throw new Error('Invalid gzip roundtrip.');
		const existing = await lstat(sidecar).catch((error) => {
			if (error.code !== 'ENOENT') throw error;
			return null;
		});
		if (existing?.isSymbolicLink()) throw new Error('Symlink is not a generic build asset.');
		await writeFile(sidecar, encoded, { flag: 'w' });
		count++;
		originalBytes += data.length;
		compressedBytes += encoded.length;
	}
}

await compressDirectory(directory);
console.log(`Public gzip: ${count} assets, ${originalBytes} → ${compressedBytes} bytes. Original files preserved.`);
