import { describe, it, expect } from 'vitest'
import {
  applyEvent,
  freshState,
  groupRows,
  isDelegation,
  toolDisplayName,
  type ChatItem,
  type ToolItem,
} from './events'
import type { ChatEvent } from '../lib/types'

// Run a sequence of events through a fresh reducer and return the items.
function reduce(events: ChatEvent[]): ChatItem[] {
  const items: ChatItem[] = []
  const st = freshState()
  for (const ev of events) applyEvent(items, st, ev)
  return items
}

describe('applyEvent', () => {
  it('returns the session id from a session event', () => {
    const st = freshState()
    expect(applyEvent([], st, { type: 'session', session_id: 'abc' })).toEqual({ session: 'abc' })
  })

  it('opens an assistant bubble on text and appends subsequent chunks to it', () => {
    const items = reduce([
      { type: 'text', text: 'Hello ' },
      { type: 'text', text: 'world' },
    ])
    expect(items).toEqual([{ kind: 'assistant', text: 'Hello world' }])
  })

  it('starts a new assistant bubble after a tool call interrupts the text', () => {
    const items = reduce([
      { type: 'text', text: 'before' },
      { type: 'tool_use', name: 'Read', input: { file: 'a.ts' }, tool_id: 't1' },
      { type: 'tool_result', tool_id: 't1', text: 'contents', is_error: false },
      { type: 'text', text: 'after' },
    ])
    expect(items.map((i) => i.kind)).toEqual(['assistant', 'tool', 'assistant'])
    expect(items[0]).toEqual({ kind: 'assistant', text: 'before' })
    expect(items[2]).toEqual({ kind: 'assistant', text: 'after' })
  })

  it('closes the open bubble on user / thinking / routed events', () => {
    const items = reduce([
      { type: 'text', text: 'a' },
      { type: 'thinking', text: 'hmm' },
      { type: 'text', text: 'b' },
      { type: 'user', text: 'question' },
      { type: 'text', text: 'c' },
      { type: 'routed', agent: 'research' },
      { type: 'text', text: 'd' },
    ])
    expect(items.map((i) => i.kind)).toEqual([
      'assistant',
      'thinking',
      'assistant',
      'user',
      'assistant',
      'routed',
      'assistant',
    ])
  })

  it('pairs a tool_result with its tool_use by id', () => {
    const items = reduce([
      { type: 'tool_use', name: 'WebSearch', input: { q: 'x' }, tool_id: 't1' },
      { type: 'tool_use', name: 'Read', input: {}, tool_id: 't2' },
      { type: 'tool_result', tool_id: 't2', text: 'file body', is_error: false },
    ])
    expect(items).toHaveLength(2)
    const [search, read] = items as Extract<ChatItem, { kind: 'tool' }>[]
    expect(search.status).toBe('running')
    expect(read.status).toBe('done')
    expect(read.resultText).toBe('file body')
    expect(read.isError).toBe(false)
  })

  it('marks an errored tool_result', () => {
    const items = reduce([
      { type: 'tool_use', name: 'Bash', input: {}, tool_id: 't1' },
      { type: 'tool_result', tool_id: 't1', text: 'boom', is_error: true },
    ])
    const tool = items[0] as Extract<ChatItem, { kind: 'tool' }>
    expect(tool.status).toBe('error')
    expect(tool.isError).toBe(true)
  })

  it('surfaces an orphan tool_result as a standalone item', () => {
    const items = reduce([{ type: 'tool_result', tool_id: 'ghost', text: 'late', is_error: false }])
    expect(items).toHaveLength(1)
    const tool = items[0] as Extract<ChatItem, { kind: 'tool' }>
    expect(tool.kind).toBe('tool')
    expect(tool.name).toBe('result')
    expect(tool.status).toBe('done')
    expect(tool.resultText).toBe('late')
  })

  it('renders result as a footer and error as an error row', () => {
    const items = reduce([
      { type: 'text', text: 'answer' },
      { type: 'result', num_turns: 3, total_cost_usd: 0.12, session_id: 's' },
      { type: 'error', message: 'nope' },
    ])
    expect(items[1]).toEqual({ kind: 'footer', numTurns: 3, cost: 0.12 })
    expect(items[2]).toEqual({ kind: 'error', message: 'nope' })
  })

  it('replays a full transcript identically to live streaming', () => {
    const stream: ChatEvent[] = [
      { type: 'session', session_id: 's1' },
      { type: 'user', text: 'hi' },
      { type: 'thinking', text: 'plan' },
      { type: 'text', text: 'part 1 ' },
      { type: 'text', text: 'part 2' },
      { type: 'tool_use', name: 'mcp__bran__spawn_agent', input: {}, tool_id: 'a' },
      { type: 'tool_result', tool_id: 'a', text: 'run_id=123', is_error: false },
      { type: 'text', text: 'done' },
      { type: 'result', num_turns: 1, total_cost_usd: null, session_id: 's1' },
      { type: 'done' },
    ]
    // Two independent reductions of the same stream must agree on everything
    // except wall-clock timing (startedAt is captured at apply time).
    const strip = (i: ChatItem) => (i.kind === 'tool' ? { ...i, startedAt: 0 } : i)
    expect(reduce(stream).map(strip)).toEqual(reduce([...stream]).map(strip))
    expect(reduce(stream).map((i) => i.kind)).toEqual([
      'user',
      'thinking',
      'assistant',
      'tool',
      'assistant',
      'footer',
    ])
  })
})

describe('toolDisplayName', () => {
  it('strips the MCP prefix down to the tool name', () => {
    expect(toolDisplayName('mcp__bran_docs__fetch_url')).toBe('fetch_url')
    expect(toolDisplayName('mcp__bran__spawn__agent')).toBe('spawn__agent')
  })
  it('leaves plain and malformed names alone', () => {
    expect(toolDisplayName('Read')).toBe('Read')
    expect(toolDisplayName('mcp__only')).toBe('mcp__only')
  })
})

describe('delegations', () => {
  it('copies the server-attached run id onto the tool item', () => {
    const items: ChatItem[] = []
    const st = freshState()
    applyEvent(items, st, {
      type: 'tool_use',
      name: 'Agent',
      input: { subagent_type: 'research', prompt: 'p' },
      tool_id: 'd1',
      run_id: 'run-123',
    })
    expect((items[0] as ToolItem).runId).toBe('run-123')
  })

  it('never folds an Agent call into a tool burst', () => {
    const mk = (name: string, id: string): ToolItem => ({
      kind: 'tool',
      toolId: id,
      name,
      input: {},
      status: 'done',
      resultText: '',
      isError: false,
      startedAt: 0,
      durationMs: null,
    })
    const rows = groupRows([mk('Read', 'a'), mk('Agent', 'b'), mk('Grep', 'c'), mk('Read', 'd')])
    expect(rows.map((r) => r.group)).toEqual([false, false, false, false])
    expect(isDelegation('Task')).toBe(true)
  })
})

describe('replayed timestamps', () => {
  it('derives a tool duration from tool_use/tool_result stamps on replay', () => {
    const items: ChatItem[] = []
    const st = freshState()
    applyEvent(items, st, {
      type: 'tool_use',
      name: 'Grep',
      input: {},
      tool_id: 't1',
      ts: '2026-07-01T10:00:00.000Z',
    })
    applyEvent(items, st, {
      type: 'tool_result',
      tool_id: 't1',
      text: 'x',
      is_error: false,
      ts: '2026-07-01T10:00:02.500Z',
    })
    const tool = items[0] as ToolItem
    expect(tool.at).toBe('2026-07-01T10:00:00.000Z')
    expect(tool.endedAt).toBe('2026-07-01T10:00:02.500Z')
    expect(tool.durationMs).toBe(2500)
  })
})
