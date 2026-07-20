// Tiny cross-component signal so UI affordances (the sidebar's search row)
// can open the command palette without owning it. The palette watches
// `requests` and opens on every increment; Ctrl/Cmd+K still works directly.

export const paletteSignal = $state({ requests: 0 })

export function requestPalette(): void {
  paletteSignal.requests++
}
