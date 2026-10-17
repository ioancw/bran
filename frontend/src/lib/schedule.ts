// Client-side checks for a runner's trigger fields, shared by the create form
// (Runners) and the edit form (RunnerDetail) so both reject the same inputs
// with the same human copy instead of round-tripping to the server — or worse,
// throwing "Invalid time value" from new Date('').

/** Returns a user-facing error for an invalid trigger, or null when valid. */
export function validateTrigger(kind: 'cron' | 'once', cron: string, runAt: string): string | null {
  if (kind === 'once') {
    if (!runAt) return 'pick a date and time for the one-shot run'
    if (new Date(runAt).getTime() < Date.now()) return 'that time is in the past — pick a future one'
  } else if (!cron.trim()) {
    return 'give the runner a schedule'
  }
  return null
}

/** datetime-local floor for one-shot runners — scheduling into the past
 *  either never fires or fires instantly, both surprising. */
export function nowLocal(): string {
  const d = new Date()
  d.setSeconds(0, 0)
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

const DAY_LABELS: Record<string, string> = {
  mon: 'Monday',
  tue: 'Tuesday',
  wed: 'Wednesday',
  thu: 'Thursday',
  fri: 'Friday',
  sat: 'Saturday',
  sun: 'Sunday',
}

const pad2 = (s: string) => String(parseInt(s, 10)).padStart(2, '0')
const at = (h: string, m: string) => `${pad2(h)}:${pad2(m)}`
const isNum = (s: string) => /^\d+$/.test(s)

/** Plain-English description of a 5-field cron, mirroring the backend's
 * `humanize_cron` (src/bran/nl_cron.py) so tables and the chat echo the same
 * words. Returns the raw string unchanged when no pattern matches. */
export function describeCron(cron: string): string {
  const parts = cron.trim().split(/\s+/)
  if (parts.length !== 5) return cron
  const [minute, hour, dom, month, dow] = parts
  const star = (s: string) => s === '*'
  if (minute.startsWith('*/') && star(hour) && star(dom) && star(month) && star(dow))
    return `Every ${minute.slice(2)} minutes`
  if (minute === '0' && hour.startsWith('*/') && star(dom) && star(month) && star(dow))
    return `Every ${hour.slice(2)} hours`
  if (minute === '0' && star(hour) && star(dom) && star(month) && star(dow)) return 'Every hour on the hour'
  if (isNum(minute) && isNum(hour) && star(dom) && star(month)) {
    const d = dow.toLowerCase()
    if (star(dow)) return `Every day at ${at(hour, minute)}`
    if (d === 'mon-fri') return `Every weekday at ${at(hour, minute)}`
    if (d === 'sat,sun' || d === 'sat-sun') return `Every weekend at ${at(hour, minute)}`
    if (d in DAY_LABELS) return `Every ${DAY_LABELS[d]} at ${at(hour, minute)}`
  }
  if (isNum(minute) && isNum(hour) && dom === '1' && star(month) && star(dow))
    return `First of every month at ${at(hour, minute)}`
  return cron
}
