# bran frontend (Svelte)

A Svelte 5 + TypeScript SPA, built with Vite, served by FastAPI at the site
root **`/`**. This is the bran web UI (it replaced the original HTMX/Jinja UI at
cutover). Backend stays Python — the SPA only consumes the `/spa/*` JSON API.

## Architecture

- **One event schema.** `bran.web.events` normalizes both the live SDK stream
  and replayed transcripts into a single shape (`src/lib/types.ts` `ChatEvent`).
  `src/chat/events.ts` folds that stream into a render-model the UI draws once —
  no more separate live/replay rendering paths.
- **Design system reuse.** `src/styles/global.css` + `themes.css` are copied from
  the Python package, so the SPA matches the existing look and the 5 themes.
- **Router.** Tiny history router (`src/lib/router.svelte.ts`); routes are
  app-relative under `/app`.

## Develop

Run the backend and the Vite dev server side by side:

```bash
# terminal 1 — backend (proxied for /spa, /static)
bran serve                    # or: uvicorn bran.api:app --port 8765

# terminal 2 — frontend with HMR
cd frontend && npm install && npm run dev
# open http://localhost:5173/
```

## Build (ship it)

```bash
cd frontend && npm run build  # type-checks, then emits into ../src/bran/web/spa/
bran serve                    # now serves the SPA at http://127.0.0.1:8765/
```

`npm run build` runs `svelte-check` first, so type errors fail the build.
`npm run gen:types` regenerates `src/lib/api-types.ts` from FastAPI's OpenAPI
(backend must be running).
