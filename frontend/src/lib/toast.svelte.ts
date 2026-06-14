// Global toast notifications — one consistent home for "it worked" feedback
// (copied, saved, paused…) and transient errors, instead of per-page flashes.

export interface Toast {
  id: number
  text: string
  tone: 'info' | 'ok' | 'err'
}

let nextId = 0

export const toastState = $state<{ items: Toast[] }>({ items: [] })

export function toast(text: string, tone: Toast['tone'] = 'info', duration = 2600): void {
  const id = ++nextId
  toastState.items.push({ id, text, tone })
  // Errors linger a little longer — they carry information, not just reassurance.
  setTimeout(dismiss, tone === 'err' ? Math.max(duration, 5000) : duration, id)
}

export function dismiss(id: number): void {
  toastState.items = toastState.items.filter((t) => t.id !== id)
}
