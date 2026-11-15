import { describe, it, expect } from 'vitest'
import type { ChatItem, ToolItem } from './events'
import { activityDuration, activitySummary, fmtSpan, stepLabel, turnStats, turnSummary } from './activity'

function tool(name: string, input: Record<string, unknown> = {}, extra: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: 'tool',
    toolId: null,
    name,
    input,
    status: 'done',
    resultText: '',
    isError: false,
    startedAt: 0,
    durationMs: null,
    ...extra,
  }
}

describe('stepLabel', () => {
  it('says what a step is doing in plain words', () => {
    expect(stepLabel({ kind: 'thinking', text: '…' })).toBe('Thinking')
    expect(stepLabel(tool('WebSearch', { query: 'brent crude today' }))).toBe(
      'Searching the web for “brent crude today”',
    )
    expect(stepLabel(tool('mcp__bran_docs__fetch_url', { url: 'https://www.bbc.co.uk/news' }))).toBe(
      'Reading bbc.co.uk',
    )
    expect(stepLabel(tool('mcp__tavily__tavily_extract', { urls: ['https://ft.com/a'] }))).toBe(
      'Reading ft.com',
    )
    expect(stepLabel(tool('Read', { file_path: '/home/u/proj/notes/a.md' }))).toBe('Reading …/notes/a.md')
    expect(stepLabel(tool('Grep', { pattern: 'TODO' }))).toBe('Searching files for “TODO”')
    expect(stepLabel(tool('Write', { file_path: 'out.md' }))).toBe('Writing out.md')
    expect(stepLabel(tool('Bash', { command: 'ls' }))).toBe('Running a command')
    expect(stepLabel(tool('mcp__bran__list_runs'))).toBe('Using list_runs')
    expect(stepLabel(tool('WebFetch'))).toBe('Reading a page')
  })
})

describe('activitySummary', () => {
  it('counts by kind of work', () => {
    const steps = [
      tool('WebSearch'),
      tool('WebSearch'),
      tool('mcp__tavily__tavily_search'),
      tool('WebFetch'),
      tool('Read'),
      tool('Read'),
      tool('Bash'),
      { kind: 'thinking' as const, text: 'x' },
    ]
    expect(activitySummary(steps)).toBe('3 web searches · 1 page read · 2 files read · 1 command')
  })
  it('names failures and other tools', () => {
    expect(activitySummary([tool('mcp__bran__list_runs'), tool('Read', {}, { isError: true })])).toBe(
      '1 file read · 1 other step · 1 failed',
    )
    expect(activitySummary([tool('mcp__bran__list_runs'), tool('mcp__bran__get_run')])).toBe('2 steps')
  })
  it('thinking alone is still something', () => {
    expect(activitySummary([{ kind: 'thinking', text: 'x' }])).toBe('Thought it through')
  })
})

describe('durations', () => {
  it('fmtSpan', () => {
    expect(fmtSpan(400)).toBe('1s')
    expect(fmtSpan(42_400)).toBe('42s')
    expect(fmtSpan(65_000)).toBe('1m 05s')
    expect(fmtSpan(3_725_000)).toBe('1h 02m')
  })
  it('activityDuration adds what is known', () => {
    expect(
      activityDuration([tool('Read', {}, { durationMs: 1200 }), tool('Read', {}, { durationMs: 800 })]),
    ).toBe(2000)
    expect(activityDuration([tool('Read'), { kind: 'thinking', text: 'x' }])).toBeNull()
  })
})

describe('turnStats / turnSummary', () => {
  const items: ChatItem[] = [
    { kind: 'user', text: 'old', at: '2026-07-01T09:00:00Z' },
    { kind: 'user', text: 'go', at: '2026-07-01T10:00:00Z' },
    tool('Agent', {}, { at: '2026-07-01T10:00:05Z', endedAt: '2026-07-01T10:00:30Z' }),
    tool('mcp__bran__spawn_agent'),
    tool('Read'),
    { kind: 'assistant', text: 'done', at: '2026-07-01T10:00:40Z' },
    { kind: 'footer', numTurns: 3, cost: 0.0123, at: '2026-07-01T10:00:42Z' },
  ]
  it('measures the turn it is given and counts the agents it used', () => {
    expect(turnStats(items, 1, items.length)).toEqual({ durationMs: 42_000, agents: 2 })
  })
  it('has no duration without timestamps', () => {
    expect(
      turnStats(
        [
          { kind: 'user', text: 'x' },
          { kind: 'assistant', text: 'y' },
        ],
        0,
        2,
      ),
    ).toEqual({
      durationMs: null,
      agents: 0,
    })
  })
  it('writes the closing line', () => {
    expect(turnSummary({ durationMs: 42_000, agents: 3 }, 6, 0.0123)).toBe(
      'Worked for 42s · 6 sources · 3 agents · $0.0123',
    )
    expect(turnSummary({ durationMs: null, agents: 0 }, 1, null)).toBe('Done · 1 source')
  })
})
