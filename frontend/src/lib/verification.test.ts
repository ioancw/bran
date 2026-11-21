import { describe, it, expect } from 'vitest'
import { verifyBadge } from './verification'
import type { RunRecord } from './types'

const run = (verification?: unknown) =>
  ({ metadata: verification ? { verification } : {} }) as unknown as RunRecord

describe('verifyBadge', () => {
  it('says how the review went', () => {
    expect(verifyBadge(run())).toBeNull()
    expect(verifyBadge(run({ status: 'done', pass: true }))?.label).toBe('Verified')
    expect(verifyBadge(run({ status: 'done', pass: true, retry_of: 'a' }))?.label).toBe('Revised')
    expect(verifyBadge(run({ status: 'done', pass: false }))).toMatchObject({
      label: 'Unverified',
      tone: 'warn',
    })
  })
  it('flags a report whose review could not run', () => {
    const b = verifyBadge(run({ status: 'skipped', reason: 'critic failed', retry_of: 'a' }))
    expect(b).toMatchObject({ label: 'Not reviewed', tone: 'warn' })
    expect(b?.hint).toBeTruthy()
  })
})
