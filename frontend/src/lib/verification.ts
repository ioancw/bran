// Helpers for reading run annotations — the evaluator verdict (verify-mode
// runners) and the alert marker (alert-mode runners) — shared by the reading
// surfaces (Outputs, Today) and the run detail page.

import type { RunRecord, Verification } from './types'

// Keep in sync with ALERT_MARKER in src/bran/notify.py.
const ALERT_MARKER = '🚨 ALERT'

/** A completed alert-mode run whose report crossed its significance bar. */
export function isAlertResult(r: RunRecord): boolean {
  return r.status === 'completed' && (r.result ?? '').trimStart().startsWith(ALERT_MARKER)
}

export function verificationOf(r: RunRecord): Verification | null {
  const v = r.metadata?.verification
  return v && typeof v === 'object' ? (v as Verification) : null
}

/** A run whose failed verdict triggered a corrected attempt. The corrected run
 * is the one worth reading — reading surfaces hide the superseded original. */
export function isSuperseded(r: RunRecord): boolean {
  const v = verificationOf(r)
  return !!v && v.status === 'done' && v.pass === false && !!v.retried_run_id
}

/** Badge for reading surfaces: Verified / Revised (passed after a retry) /
 * Unverified (failed review with no successful retry) / Not reviewed (the
 * review was due but could not run). Null = this run is not one that gets
 * reviewed. `hint` says what the label means, for a tooltip. */
export function verifyBadge(r: RunRecord): { label: string; tone: 'ok' | 'warn'; hint: string } | null {
  const v = verificationOf(r)
  if (!v) return null
  if (v.status !== 'done')
    return {
      label: 'Not reviewed',
      tone: 'warn',
      hint: 'The review could not run, so nobody checked this report',
    }
  if (v.pass)
    return v.retry_of
      ? { label: 'Revised', tone: 'ok', hint: 'Rewritten after a reviewer found problems, then passed' }
      : { label: 'Verified', tone: 'ok', hint: 'A reviewer agent checked this report' }
  return { label: 'Unverified', tone: 'warn', hint: 'A reviewer agent found problems with this report' }
}
