import './app.css'
import 'katex/dist/katex.min.css' // KaTeX needs its stylesheet (+ bundled fonts) to lay math out
import { mount } from 'svelte'
import App from './App.svelte'
import { initTheme } from './lib/theme'

initTheme()

const app = mount(App, { target: document.getElementById('app')! })

export default app
