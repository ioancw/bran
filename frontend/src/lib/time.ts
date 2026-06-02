// Small time/format helpers shared across pages.

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  const s = Math.max(1, Math.floor((Date.now() - then) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function shortId(id: string, n = 8): string {
  return id.slice(0, n)
}

export function fmtCost(v: number | null | undefined): string {
  return v != null ? `$${v.toFixed(4)}` : '—'
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return `${(ms / 1000).toFixed(1)}s`
}

export function localDateTime(iso: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 19)
}
