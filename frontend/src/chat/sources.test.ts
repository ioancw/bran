import { describe, it, expect } from 'vitest'
import type { ChatItem, ToolItem } from './events'
import type { SourceRef } from '../lib/types'
import { citedDomains, domainHue, domainOf, normUrl, pathLabel, sourcesInRange, turnRanges } from './sources'

function fetchTool(url: string | string[], name = 'WebFetch'): ToolItem {
  return {
    kind: 'tool',
    toolId: null,
    name,
    input: Array.isArray(url) ? { urls: url } : { url },
    status: 'done',
    resultText: '',
    isError: false,
    startedAt: 0,
    durationMs: null,
  }
}

describe('url helpers', () => {
  it('domainOf drops www and tolerates junk', () => {
    expect(domainOf('https://www.BBC.co.uk/news')).toBe('bbc.co.uk')
    expect(domainOf('not a url')).toBe('')
  })
  it('normUrl makes one key per page', () => {
    expect(normUrl('https://WWW.ft.com/content/abc/')).toBe('https://www.ft.com/content/abc')
    expect(normUrl('https://ft.com/a?x=1.')).toBe('https://ft.com/a?x=1')
  })
  it('citedDomains splits a citation list', () => {
    expect(citedDomains('bbc.co.uk, `www.ft.com` and reuters.com')).toEqual([
      'bbc.co.uk',
      'ft.com',
      'reuters.com',
    ])
    expect(citedDomains('internal notes')).toEqual([])
  })
  it('pathLabel and domainHue are stable', () => {
    expect(pathLabel('https://bbc.co.uk/news/business/live/')).toBe('business / live')
    expect(pathLabel('https://bbc.co.uk/')).toBe('')
    expect(domainHue('bbc.co.uk')).toBe(domainHue('bbc.co.uk'))
    expect(domainHue('bbc.co.uk')).toBeLessThan(360)
  })
})

describe('sourcesInRange', () => {
  it('lists fetched pages, linked pages and cited domains in order of appearance', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'markets?' },
      fetchTool('https://www.bbc.co.uk/news/business'),
      fetchTool(['https://ft.com/markets', 'ftp://nope']),
      {
        kind: 'assistant',
        text: 'Oil fell ([Reuters](https://www.reuters.com/oil)). (Source: ft.com) See https://example.org/a.',
      },
    ]
    const got = sourcesInRange(items, 0, items.length)
    expect(got.map((s) => [s.domain, s.via, s.title])).toEqual([
      ['bbc.co.uk', 'fetched', null],
      ['ft.com', 'cited', null], // fetched, then cited by domain
      ['reuters.com', 'cited', 'Reuters'],
      ['example.org', 'cited', null],
    ])
    expect(got[3].url).toBe('https://example.org/a')
  })

  it('never reorders what is already listed as a reply grows', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'q' },
      fetchTool('https://a.com/1'),
      { kind: 'assistant', text: 'First' },
    ]
    const before = sourcesInRange(items, 0, items.length).map((s) => s.domain)
    ;(items[2] as { text: string }).text += ' [b](https://b.com/x) and (Source: a.com)'
    const after = sourcesInRange(items, 0, items.length).map((s) => s.domain)
    expect(after.slice(0, before.length)).toEqual(before)
    expect(after).toEqual(['a.com', 'b.com'])
  })

  it('uses the known list for titles and for the page behind a cited domain', () => {
    const known: SourceRef[] = [
      {
        url: 'https://www.bbc.co.uk/news/business',
        domain: 'bbc.co.uk',
        title: 'Business news',
        via: 'fetched',
      },
      { url: 'https://ons.gov.uk/cpi', domain: 'ons.gov.uk', title: 'CPI', via: 'fetched' },
    ]
    const items: ChatItem[] = [
      fetchTool('https://www.bbc.co.uk/news/business/'),
      { kind: 'assistant', text: 'Inflation eased (Source: ons.gov.uk) (Source: unknown.example)' },
    ]
    const got = sourcesInRange(items, 0, 2, known)
    expect(got.map((s) => [s.domain, s.url, s.title])).toEqual([
      ['bbc.co.uk', 'https://www.bbc.co.uk/news/business/', 'Business news'],
      ['ons.gov.uk', 'https://ons.gov.uk/cpi', 'CPI'],
      ['unknown.example', null, null],
    ])
  })

  it('only looks inside the range it is given', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'one' },
      fetchTool('https://a.com/1'),
      { kind: 'user', text: 'two' },
      fetchTool('https://b.com/1'),
    ]
    expect(sourcesInRange(items, 2, 4).map((s) => s.domain)).toEqual(['b.com'])
  })
})

describe('turnRanges', () => {
  it('splits at user messages', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'a' },
      { kind: 'assistant', text: 'x' },
      { kind: 'user', text: 'b' },
      { kind: 'assistant', text: 'y' },
      { kind: 'footer', numTurns: 1, cost: null },
    ]
    expect(turnRanges(items)).toEqual([
      { start: 0, end: 2 },
      { start: 2, end: 5 },
    ])
  })
  it('gives a transcript with no user message one turn', () => {
    expect(turnRanges([{ kind: 'assistant', text: 'x' }])).toEqual([{ start: 0, end: 1 }])
    expect(turnRanges([])).toEqual([])
  })
  it('keeps what precedes the first user message as its own turn', () => {
    const items: ChatItem[] = [
      { kind: 'routed', agent: 'research' },
      { kind: 'user', text: 'a' },
    ]
    expect(turnRanges(items)).toEqual([
      { start: 0, end: 1 },
      { start: 1, end: 2 },
    ])
  })
})
