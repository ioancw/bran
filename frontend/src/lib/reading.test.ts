import { describe, it, expect } from 'vitest'
import { dateline, readMinutes, splitHeadline } from './reading'

describe('splitHeadline', () => {
  it('lifts a leading top-level heading out of the body', () => {
    expect(splitHeadline('# Morning brief\n\nOil fell.\n\n## FX\nSterling rose.')).toEqual({
      headline: 'Morning brief',
      body: 'Oil fell.\n\n## FX\nSterling rose.',
    })
    expect(splitHeadline('\n\n# **Brief** for `Monday` #\nBody')).toEqual({
      headline: 'Brief for Monday',
      body: 'Body',
    })
  })
  it('lifts a heading that follows a short preamble, and keeps the preamble', () => {
    const md =
      'Briefing saved to `/x/brief.md`. Here is the full report:\n\n---\n\n# Finance Briefing\n\n**Lens:** FX'
    expect(splitHeadline(md)).toEqual({
      headline: 'Finance Briefing',
      body: 'Briefing saved to `/x/brief.md`. Here is the full report:\n\n**Lens:** FX',
    })
  })
  it('leaves everything else alone', () => {
    const late = 'Oil fell. '.repeat(80) + '\n\n# Much later heading\nMore.'
    expect(splitHeadline(late)).toEqual({ headline: null, body: late })
    const fenced = '```sh\n# a comment\n```\nBody'
    expect(splitHeadline(fenced)).toEqual({ headline: null, body: fenced })
    expect(splitHeadline('## Section first\nBody').headline).toBeNull()
    expect(splitHeadline('#hashtag not a heading').headline).toBeNull()
    expect(splitHeadline('').headline).toBeNull()
  })
  it('handles a heading with nothing after it', () => {
    expect(splitHeadline('# Only a title')).toEqual({ headline: 'Only a title', body: '' })
  })
})

describe('readMinutes', () => {
  it('counts words, not markup', () => {
    expect(readMinutes('short')).toBe(1)
    expect(readMinutes(Array(660).fill('word').join(' '))).toBe(3)
    expect(readMinutes('--- | --- | ---\n* * *')).toBe(1)
  })
})

describe('dateline', () => {
  it('is empty for junk and names the year only when it differs', () => {
    expect(dateline(null)).toBe('')
    expect(dateline('nope')).toBe('')
    const now = new Date('2026-11-16T12:00:00Z')
    expect(dateline('2026-11-16T07:02:00Z', now)).not.toMatch(/2026/)
    expect(dateline('2025-11-16T07:02:00Z', now)).toMatch(/2025/)
    expect(dateline('2026-11-16T07:02:00Z', now)).toContain(' · ')
  })
})
