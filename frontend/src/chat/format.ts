// Chat-local formatting helpers: timestamps for day dividers and hover times,
// and the stricter "did this shell output actually fail" heuristic.

/** HH:MM in the viewer's locale for an ISO timestamp ('' if unparseable). */
export function clock(iso: string | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** Full local date + time for a title attribute. */
export function fullStamp(iso: string | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Local calendar-day key (YYYY-MM-DD) — the unit a day divider is keyed on. */
export function dayKey(iso: string | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d.getTime())) return null
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** "Today", "Yesterday", or "Saturday 25 July" (year added when it differs). */
export function dayLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const key = dayKey(iso)
  if (key === dayKey(now.toISOString())) return 'Today'
  const y = new Date(now)
  y.setDate(y.getDate() - 1)
  if (key === dayKey(y.toISOString())) return 'Yesterday'
  const opts: Intl.DateTimeFormatOptions = { weekday: 'long', day: 'numeric', month: 'long' }
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
  return d.toLocaleDateString(undefined, opts)
}

/** Shell output that genuinely reports a failure — not merely any line that
 * happens to contain the word "error" (a grep for "error" is not an error). */
const SHELL_FAILURE = /^(error|traceback|fatal)\b|\berror:|exit code [1-9]|command not found/im
export function shellLooksFailed(text: string): boolean {
  return SHELL_FAILURE.test(text)
}
