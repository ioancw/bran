import { describe, expect, it } from 'vitest'
import { applyEvent, freshState, type ChatItem } from './events'
import {
  beginTurn,
  draftKey,
  endTurnStopped,
  endsInError,
  failTurn,
  itemsFromHistory,
  readDraft,
  spawnRunIds,
  takeRetry,
  writeDraft,
} from './turn'

function memoryStore() {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  }
}

describe('turn lifecycle', () => {
  it('a reply after Stop starts its own bubble below the new prompt', () => {
    const items: ChatItem[] = []
    const st = freshState()
    beginTurn(items, st, 'first')
    applyEvent(items, st, { type: 'text', text: 'partial ans' })
    endTurnStopped(items, st)

    beginTurn(items, st, 'second')
    applyEvent(items, st, { type: 'text', text: 'new answer' })

    expect(items.map((i) => i.kind)).toEqual(['user', 'assistant', 'footer', 'user', 'assistant'])
    expect((items[1] as { text: string }).text).toBe('partial ans')
    expect((items[4] as { text: string }).text).toBe('new answer')
  })

  it('a reply after a dropped stream does not append to the old bubble', () => {
    const items: ChatItem[] = []
    const st = freshState()
    beginTurn(items, st, 'first')
    applyEvent(items, st, { type: 'text', text: 'half' })
    // the stream dies here with nothing closing the bubble
    beginTurn(items, st, 'second')
    applyEvent(items, st, { type: 'text', text: 'whole' })
    expect((items[1] as { text: string }).text).toBe('half')
    expect((items[3] as { text: string }).text).toBe('whole')
  })

  it('failTurn leaves an error row and nothing open', () => {
    const items: ChatItem[] = []
    const st = freshState()
    beginTurn(items, st, 'q')
    applyEvent(items, st, { type: 'text', text: 'a' })
    failTurn(items, st, 'boom')
    expect(endsInError(items)).toBe(true)
    expect(st.openAssistant).toBe(-1)
  })
})

describe('takeRetry', () => {
  it('re-sends an unanswered prompt and removes both rows', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'earlier' },
      { kind: 'assistant', text: 'fine' },
      { kind: 'user', text: 'the one that failed' },
      { kind: 'error', message: 'network' },
    ]
    expect(takeRetry(items)).toBe('the one that failed')
    expect(items.map((i) => i.kind)).toEqual(['user', 'assistant'])
  })

  it('keeps a partial answer and only drops the error row', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'prompt B' },
      { kind: 'assistant', text: 'partial' },
      { kind: 'error', message: 'server said no' },
    ]
    expect(takeRetry(items)).toBe('prompt B')
    expect(items.map((i) => i.kind)).toEqual(['user', 'assistant'])
  })

  it('retries the prompt that failed, not an older one', () => {
    const items: ChatItem[] = [
      { kind: 'user', text: 'prompt A' },
      { kind: 'error', message: 'offline' },
      { kind: 'user', text: 'prompt B' },
      { kind: 'error', message: 'server error' },
    ]
    expect(takeRetry(items)).toBe('prompt B')
  })

  it('does nothing without a trailing error or without a prompt', () => {
    const ok: ChatItem[] = [
      { kind: 'user', text: 'q' },
      { kind: 'assistant', text: 'a' },
    ]
    expect(takeRetry(ok)).toBeNull()
    expect(ok).toHaveLength(2)
    const orphan: ChatItem[] = [{ kind: 'error', message: 'x' }]
    expect(takeRetry(orphan)).toBeNull()
    expect(orphan).toHaveLength(1)
  })
})

describe('itemsFromHistory', () => {
  it('replays events and hands back a closed state', () => {
    const { items, state } = itemsFromHistory([
      { type: 'user', text: 'hi' },
      { type: 'text', text: 'hello' },
    ])
    expect(items.map((i) => i.kind)).toEqual(['user', 'assistant'])
    expect(state.openAssistant).toBe(-1)
    // A live event applied next must not land in the replayed bubble.
    applyEvent(items, state, { type: 'text', text: ' again' })
    expect((items[1] as { text: string }).text).toBe('hello')
    expect(items).toHaveLength(3)
  })
})

describe('drafts', () => {
  it('keeps one draft per chat and one for a new chat', () => {
    const store = memoryStore()
    writeDraft(null, 'half a thought', store)
    writeDraft('abc', 'another', store)
    expect(draftKey(null)).toBe('bran.draft:new')
    expect(readDraft(null, store)).toBe('half a thought')
    expect(readDraft('abc', store)).toBe('another')
    expect(readDraft('other', store)).toBe('')
  })

  it('an emptied composer clears the draft', () => {
    const store = memoryStore()
    writeDraft('abc', 'text', store)
    writeDraft('abc', '   ', store)
    expect(store.data.size).toBe(0)
  })

  it('survives storage that throws', () => {
    const broken = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
      removeItem: () => {
        throw new Error('denied')
      },
    }
    expect(readDraft('x', broken)).toBe('')
    expect(() => writeDraft('x', 'y', broken)).not.toThrow()
  })
})

describe('spawnRunIds', () => {
  it('collects run ids from spawn results only', () => {
    const tool = (name: string, resultText: string): ChatItem => ({
      kind: 'tool',
      toolId: null,
      name,
      input: {},
      status: 'done',
      resultText,
      isError: false,
      startedAt: 0,
      durationMs: null,
    })
    const items: ChatItem[] = [
      tool('mcp__bran__spawn_agent', 'Spawned run_id=0a1b2c3d-1111-2222-3333-444455556666'),
      tool('Read', 'run_id=ffffffff-0000-0000-0000-000000000000'),
      tool('mcp__bran__spawn_agent', 'failed to spawn'),
    ]
    expect(spawnRunIds(items)).toEqual(['0a1b2c3d-1111-2222-3333-444455556666'])
  })
})
