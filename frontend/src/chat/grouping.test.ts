import { describe, it, expect } from 'vitest'
import { applyEvent, freshState, groupRows, type ChatItem, type ToolItem } from './events'
import { shellLooksFailed, dayKey } from './format'

function tool(name: string, id: string, isError = false): ToolItem {
  return {
    kind: 'tool',
    toolId: id,
    name,
    input: {},
    status: isError ? 'error' : 'done',
    resultText: '',
    isError,
    startedAt: 0,
    durationMs: null,
  }
}

describe('groupRows', () => {
  it('folds a burst of consecutive tool calls of mixed names into one group', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'go' },
      tool('Read', 'a'),
      tool('Grep', 'b'),
      tool('Read', 'c'),
      { kind: 'assistant', text: 'done' },
    ]
    const rows = groupRows(items)
    expect(rows.map((r) => (r.group ? 'group' : r.item.kind))).toEqual(['user', 'group', 'assistant'])
    const g = rows[1]
    expect(g.group && g.tools.map((t) => t.toolId)).toEqual(['a', 'b', 'c'])
    expect(g.idx).toBe(1)
  })

  it('folds even a single call, and the thinking around it, into one activity row', () => {
    const rows = groupRows([
      { kind: 'user', text: 'go' },
      { kind: 'thinking', text: 'hmm' },
      tool('Read', 'a'),
      { kind: 'thinking', text: 'right' },
      { kind: 'assistant', text: 'done' },
    ])
    expect(rows.map((r) => (r.group ? 'group' : r.item.kind))).toEqual(['user', 'group', 'assistant'])
    const g = rows[1]
    expect(g.group && g.steps.map((s) => s.kind)).toEqual(['thinking', 'tool', 'thinking'])
    expect(g.group && g.tools.map((t) => t.toolId)).toEqual(['a'])
    expect(g.idx).toBe(1)
  })

  it('a lone thought is an activity row too', () => {
    const rows = groupRows([
      { kind: 'thinking', text: 'hmm' },
      { kind: 'assistant', text: 'ok' },
    ])
    expect(rows.map((r) => r.group)).toEqual([true, false])
  })

  it('never groups spawn or plan cards, and they break a run', () => {
    const items: ChatItem[] = [
      tool('Read', 'a'),
      tool('Read', 'b'),
      tool('mcp__bran__spawn_agent', 's'),
      tool('Read', 'c'),
      tool('Read', 'd'),
      tool('Read', 'e'),
      tool('mcp__bran__propose_plan', 'p'),
    ]
    const rows = groupRows(items)
    expect(rows.map((r) => (r.group ? 'group' : (r.item as ToolItem).name))).toEqual([
      'group',
      'mcp__bran__spawn_agent',
      'group',
      'mcp__bran__propose_plan',
    ])
  })

  it('a saved document is its own card, never folded', () => {
    const rows = groupRows([tool('Read', 'a'), tool('mcp__bran_docs__save_document', 'd'), tool('Read', 'b')])
    expect(rows.map((r) => r.group)).toEqual([true, false, true])
  })
})

describe('timestamps', () => {
  it('carries ts from user/text events onto items, and omits it when absent', () => {
    const items: ChatItem[] = []
    const st = freshState()
    applyEvent(items, st, { type: 'user', text: 'hi', ts: '2026-07-25T09:37:27Z' })
    applyEvent(items, st, { type: 'text', text: 'a', ts: '2026-07-25T09:37:30Z' })
    applyEvent(items, st, { type: 'text', text: 'b', ts: '2026-07-25T09:37:31Z' }) // same bubble; keeps first ts
    applyEvent(items, st, { type: 'user', text: 'again' })
    expect(items).toEqual([
      { kind: 'user', text: 'hi', at: '2026-07-25T09:37:27Z' },
      { kind: 'assistant', text: 'ab', at: '2026-07-25T09:37:30Z' },
      { kind: 'user', text: 'again' },
    ])
  })
  it('dayKey is a local calendar day and null for junk', () => {
    expect(dayKey('not a date')).toBeNull()
    expect(dayKey(undefined)).toBeNull()
    expect(dayKey('2026-07-25T12:00:00Z')).toMatch(/^2026-07-2[456]$/)
  })
})

describe('shellLooksFailed', () => {
  it('ignores the word "error" in ordinary output', () => {
    expect(shellLooksFailed('grep found 3 matches for "error" in src/errors.ts')).toBe(false)
    expect(shellLooksFailed('errors.ts\nerror_handling.md')).toBe(false)
  })
  it('flags real failure signatures', () => {
    expect(shellLooksFailed('Traceback (most recent call last):\n  File "x.py"')).toBe(true)
    expect(shellLooksFailed('bash: foo: command not found')).toBe(true)
    expect(shellLooksFailed('npm ERR! exit code 1')).toBe(true)
    expect(shellLooksFailed('src/a.ts(3,1): error: TS2304')).toBe(true)
    expect(shellLooksFailed('Error: ENOENT')).toBe(true)
  })
})
