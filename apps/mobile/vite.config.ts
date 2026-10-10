import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { readFile, readdir } from 'node:fs/promises';

export default defineConfig({
  plugins: [svelte(), {
    name: 'pulse-native-notices',
    async generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'licenses/native-notices.txt',
        source: await readFile(new URL('./THIRD-PARTY-NOTICES.md', import.meta.url), 'utf8') });
      const directory = new URL('./LICENSES/', import.meta.url);
      for (const name of await readdir(directory)) {
        this.emitFile({ type: 'asset', fileName: `licenses/${name}`,
          source: await readFile(new URL(name, directory), 'utf8') });
      }
    }
  }],
  publicDir: '../web/public',
  resolve: { dedupe: ['svelte'] },
  build: { target: 'es2022' }
});
