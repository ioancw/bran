// Helpers for reading the evaluator verdict (verify-mode runners) off a run,
// shared by the reading surfaces (Outputs, Today) and the run detail page.

import type { RunRecord, Verification } from './types'

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

/** Badge for reading surfaces: ✓ verified / ✓ revised (passed after a retry) /
 * ✗ unverified (failed review with no successful retry). Null = not reviewed. */
export function verifyBadge(r: RunRecord): { label: string; tone: 'ok' | 'warn' } | null {
  const v = verificationOf(r)
  if (!v || v.status !== 'done') return null
  if (v.pass) return { label: v.retry_of ? '✓ revised' : '✓ verified', tone: 'ok' }
  return { label: '✗ unverified', tone: 'warn' }
}
