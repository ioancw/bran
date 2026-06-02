import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import path from 'node:path'

// The SPA is served by FastAPI under /app, building into the Python package so
// it ships with bran. In dev, Vite serves it and proxies API + static to the
// running `bran serve` backend on :8765.
export default defineConfig({
  base: '/',
  plugins: [svelte()],
  build: {
    outDir: path.resolve(__dirname, '../src/bran/web/spa'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/spa': 'http://127.0.0.1:8765',
      '/static': 'http://127.0.0.1:8765',
      '/healthz': 'http://127.0.0.1:8765',
    },
  },
})
