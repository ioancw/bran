// Theme handling, ported from base.html's inline script. Persists choice to
// localStorage, auto-detects OS preference, applies before paint to avoid flash.

export interface Theme {
  name: string
  label: string
  // Preview colours for the Settings appearance picker — each theme's
  // --bg / --surface / --fg / --accent (keep in sync with styles/themes.css).
  bg: string
  surface: string
  fg: string
  accent: string
}

export const THEMES: Theme[] = [
  {
    name: 'midnight',
    label: 'Midnight',
    bg: '#0c0c0e',
    surface: '#15151b',
    fg: '#c4c4d4',
    accent: '#c17b5b',
  },
  { name: 'light', label: 'Light', bg: '#faf0e6', surface: '#fdf5ec', fg: '#4a4138', accent: '#a04a5f' },
  { name: 'dracula', label: 'Dracula', bg: '#282a36', surface: '#2d2f3d', fg: '#cdd6f4', accent: '#bd93f9' },
  {
    name: 'solarized',
    label: 'Solarized',
    bg: '#002b36',
    surface: '#073642',
    fg: '#93a1a1',
    accent: '#268bd2',
  },
  { name: 'nord', label: 'Nord', bg: '#2e3440', surface: '#3b4252', fg: '#d8dee9', accent: '#88c0d0' },
]

const KEY = 'bran_theme'

function apply(name: string): void {
  if (name === 'midnight' || !name) {
    document.documentElement.removeAttribute('data-theme')
  } else {
    document.documentElement.setAttribute('data-theme', name)
  }
  // Keep the browser chrome (mobile address bar etc.) on the theme's canvas.
  const bg = THEMES.find((t) => t.name === name)?.bg ?? THEMES[0].bg
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', bg)
}

export function getTheme(): string {
  return localStorage.getItem(KEY) || 'midnight'
}

export function setTheme(name: string): void {
  localStorage.setItem(KEY, name)
  apply(name)
}

export function initTheme(): void {
  const saved = localStorage.getItem(KEY)
  if (saved) {
    apply(saved)
  } else {
    const preferLight = window.matchMedia('(prefers-color-scheme: light)').matches
    apply(preferLight ? 'light' : 'midnight')
  }
}
