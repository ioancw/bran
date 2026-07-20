// Theme handling, ported from base.html's inline script. Persists choice to
// localStorage, auto-detects OS preference, applies before paint to avoid flash.

export interface Theme {
  name: string
  label: string
  // Swatch colours for the sidebar theme picker — each theme's --bg / --accent
  // (keep in sync with styles/themes.css).
  bg: string
  accent: string
}

export const THEMES: Theme[] = [
  { name: 'midnight', label: 'Midnight', bg: '#0c0c0e', accent: '#c17b5b' },
  { name: 'light', label: 'Light', bg: '#faf0e6', accent: '#a04a5f' },
  { name: 'dracula', label: 'Dracula', bg: '#282a36', accent: '#bd93f9' },
  { name: 'solarized', label: 'Solarized', bg: '#002b36', accent: '#268bd2' },
  { name: 'nord', label: 'Nord', bg: '#2e3440', accent: '#88c0d0' },
]

const KEY = 'bran_theme'

function apply(name: string): void {
  if (name === 'midnight' || !name) {
    document.documentElement.removeAttribute('data-theme')
  } else {
    document.documentElement.setAttribute('data-theme', name)
  }
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
