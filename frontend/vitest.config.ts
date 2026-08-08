import { defineConfig } from 'vitest/config'

// Pure-module tests only (reducers, markdown pipeline) — no component
// rendering, so the svelte plugin isn't needed. jsdom provides the DOM that
// DOMPurify requires — pinned to v24: newer jsdom pulls an ESM-only dep that
// breaks under Node 18's require(), and happy-dom miscarries DOMPurify's node
// traversal (spuriously strips H1). CSS imports (katex) are no-ops here.
export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
  },
})
