import { describe, expect, it } from 'vitest'
import { describeCron, validateTrigger } from './schedule'

describe('describeCron', () => {
  it('describes the common shapes humanize_cron knows', () => {
    expect(describeCron('*/15 * * * *')).toBe('Every 15 minutes')
    expect(describeCron('0 */6 * * *')).toBe('Every 6 hours')
    expect(describeCron('0 * * * *')).toBe('Every hour on the hour')
    expect(describeCron('30 7 * * *')).toBe('Every day at 07:30')
    expect(describeCron('0 7 * * mon-fri')).toBe('Every weekday at 07:00')
    expect(describeCron('15 7 * * sat,sun')).toBe('Every weekend at 07:15')
    expect(describeCron('0 9 * * mon')).toBe('Every Monday at 09:00')
    expect(describeCron('0 9 1 * *')).toBe('First of every month at 09:00')
  })
  it('returns the raw string when it cannot describe it', () => {
    expect(describeCron('5 4 * * 2')).toBe('5 4 * * 2')
    expect(describeCron('not a cron')).toBe('not a cron')
    expect(describeCron('')).toBe('')
  })
})

describe('validateTrigger', () => {
  it('rejects an empty cron and a missing one-shot time', () => {
    expect(validateTrigger('cron', '', '')).not.toBeNull()
    expect(validateTrigger('once', '', '')).not.toBeNull()
    expect(validateTrigger('cron', '0 8 * * *', '')).toBeNull()
  })
})
