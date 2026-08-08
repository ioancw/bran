import './app.css'
// Brand fonts, self-hosted (bundled woff2) — the Google Fonts CDN gave a
// visible FOUT on the serif mastheads and lost the identity offline.
// Weights mirror what the old CDN link loaded; keep in sync with themes.css.
import '@fontsource/schibsted-grotesk/400.css'
import '@fontsource/schibsted-grotesk/500.css'
import '@fontsource/schibsted-grotesk/600.css'
import '@fontsource/schibsted-grotesk/700.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import '@fontsource/geist-mono/600.css'
import '@fontsource/source-serif-4/400.css'
// 500 is the mastheads' declared weight (.page-header h1, .wordmark) — without
// this file the browser silently resolves those to 400.
import '@fontsource/source-serif-4/500.css'
import '@fontsource/source-serif-4/600.css'
import '@fontsource/source-serif-4/400-italic.css'
import '@fontsource/source-serif-4/500-italic.css'
import '@fontsource/source-serif-4/600-italic.css'
// (KaTeX's stylesheet moved into the lazy markdown chunk — see lib/markdown.ts.)
import { mount } from 'svelte'
import App from './App.svelte'
import { initTheme } from './lib/theme'

initTheme()

const app = mount(App, { target: document.getElementById('app')! })

export default app
