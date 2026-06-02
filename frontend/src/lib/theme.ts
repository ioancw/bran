// Theme handling, ported from base.html's inline script. Persists choice to
// localStorage, auto-detects OS preference, applies before paint to avoid flash.

export interface Theme {
  name: string
  label: string
}

export const THEMES: Theme[] = [
  { name: 'midnight', label: 'Midnight' },
  { name: 'light', label: 'Light' },
  { name: 'dracula', label: 'Dracula' },
  { name: 'solarized', label: 'Solarized' },
  { name: 'nord', label: 'Nord' },
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
