import './app.css'
// Brand fonts, self-hosted (bundled woff2) — no CDN, no FOUT, works offline.
// Schibsted Grotesk is the UI face; Geist Mono for identifiers and code;
// Source Serif 4 only for the reading column (.prose-md.reading). See DESIGN.md.
import '@fontsource/schibsted-grotesk/400.css'
import '@fontsource/schibsted-grotesk/500.css'
import '@fontsource/schibsted-grotesk/600.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import '@fontsource/source-serif-4/400.css'
import '@fontsource/source-serif-4/600.css'
import '@fontsource/source-serif-4/400-italic.css'
// (KaTeX's stylesheet moved into the lazy markdown chunk — see lib/markdown.ts.)
import { mount } from 'svelte'
import App from './App.svelte'
import { initTheme } from './lib/theme'

initTheme()

const app = mount(App, { target: document.getElementById('app')! })

export default app
