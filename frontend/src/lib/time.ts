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

/** "in 2h 14m" countdown to a future ISO timestamp; pass `nowMs` so a ticking
 * $state can keep it live. */
export function countdown(iso: string, nowMs: number = Date.now()): string {
  const s = Math.floor((new Date(iso).getTime() - nowMs) / 1000)
  if (s < 0) return 'overdue'
  if (s < 60) return 'now'
  if (s < 3600) return `in ${Math.floor(s / 60)}m`
  if (s < 86400) return `in ${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  return `in ${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

/** Local wall-clock time (HH:MM) for an ISO timestamp — unlike localDateTime,
 * this respects the timezone offset in the string. */
export function localClock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}
