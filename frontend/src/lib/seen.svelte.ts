// "New since last visit" tracking for Outputs. A localStorage timestamp marks
// the last time the Outputs page was opened: the sidebar badge counts outputs
// that finished after it, and opening Outputs advances it (clearing the badge)
// while the page highlights what was new at the moment it opened.

const KEY = 'bran.outputsSeenAt'

function read(): number {
  try {
    const v = Number(localStorage.getItem(KEY))
    return Number.isFinite(v) && v > 0 ? v : 0
  } catch {
    return 0
  }
}

export const outputsSeen = $state({ at: read() })

/**
 * Advance the marker to now and return the previous value, so the page can
 * still highlight what was new when it opened. A first-ever visit returns 0,
 * which `isNewSince` treats as "nothing is new" (not "everything is").
 */
export function markOutputsSeen(): number {
  const prev = outputsSeen.at
  outputsSeen.at = Date.now()
  try {
    localStorage.setItem(KEY, String(outputsSeen.at))
  } catch {
    /* storage unavailable — the badge just never clears */
  }
  return prev
}

export function isNewSince(iso: string, since: number): boolean {
  return since > 0 && new Date(iso).getTime() > since
}
