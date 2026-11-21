import { describe, it, expect } from 'vitest'
import { backLink, dateline, excerpt, originOf, readMinutes, splitHeadline } from './reading'

describe('backLink', () => {
  it('goes back to where the reader came from, and says where', () => {
    expect(backLink('/runs/abc')).toEqual({ to: '/runs/abc', label: 'Back to the run' })
    expect(backLink('/')).toEqual({ to: '/', label: 'Back to Today' })
    expect(backLink('/chat/c1', 'News agents')).toEqual({ to: '/chat/c1', label: 'Back to News agents' })
    expect(backLink('/chat/c1').label).toBe('Back to the chat')
    expect(backLink('/runs?status=failed')).toEqual({ to: '/runs?status=failed', label: 'Back to Runs' })
    expect(backLink('/somewhere-new').label).toBe('Back')
  })
  it('falls back to Outputs when the report was opened directly', () => {
    expect(backLink(null)).toEqual({ to: '/outputs', label: 'Outputs' })
    expect(backLink('/outputs')).toEqual({ to: '/outputs', label: 'Outputs' })
    expect(backLink('/read/older')).toEqual({ to: '/outputs', label: 'Outputs' })
  })
  it('keeps the starting page while stepping between editions', () => {
    expect(originOf('/runs/abc', '/', '/read/abc')).toBe('/runs/abc')
    expect(originOf('/read/abc', '/runs/abc', '/read/older')).toBe('/runs/abc')
    expect(originOf('/read/abc', '/runs/abc', '/runs/abc')).toBe('/read/abc')
  })
})

describe('excerpt', () => {
  it('opens with the report, not the note above its title', () => {
    const md =
      'Fact-check done: 10 of 12 claims held. Saved at `/home/x/b.md`.\n\n' +
      '# Finance Briefing\n**Lens:** FX volatility\n\n> **Delta note:** only changes.\n\n' +
      '## What changed\n- **NEW: Nvidia reported.** It beat on [sales](https://x.test).\n- Sterling slipped.'
    expect(excerpt(md)).toBe('NEW: Nvidia reported. It beat on sales. Sterling slipped.')
  })
  it('reads plain prose as it is, and cuts at a word', () => {
    expect(excerpt('Rates were steady.\n\n| a | b |\n\n```\ncode\n```\nNothing moved.')).toBe(
      'Rates were steady. Nothing moved.',
    )
    const long = excerpt('word '.repeat(200), 50)
    expect(long.length).toBeLessThanOrEqual(50)
    expect(long.endsWith('…')).toBe(true)
  })
  it('falls back to the raw start when nothing reads as prose', () => {
    expect(excerpt('# Only a title')).toBe('# Only a title')
  })
})

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
