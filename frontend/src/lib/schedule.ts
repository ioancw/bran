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
