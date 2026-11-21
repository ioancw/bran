import { describe, it, expect } from 'vitest'
import { isTeammateActive, teammateHint, teammateHref } from './teammates'

const NOW = Date.parse('2026-11-15T21:00:00Z')

describe('teammates', () => {
  it('says what a teammate is doing, else when it is next due', () => {
    expect(teammateHint({ state: 'working', next_run: '2026-11-16T07:00:00Z' }, NOW)).toBe('Working now')
    expect(teammateHint({ state: 'failed', next_run: '2026-11-16T07:00:00Z' }, NOW)).toBe('failed')
    expect(teammateHint({ state: 'paused', next_run: null }, NOW)).toBe('Paused')
    expect(teammateHint({ state: 'ready', next_run: '2026-11-16T07:00:00Z' }, NOW)).toBe('in 10h 0m')
    expect(teammateHint({ state: 'ready', next_run: null }, NOW)).toBe('') // on demand only
    // A question waiting for the user comes before when it is next due.
    expect(teammateHint({ state: 'ready', next_run: '2026-11-16T07:00:00Z', waiting: 1 }, NOW)).toBe(
      'Asked you',
    )
    expect(teammateHint({ state: 'working', next_run: null, waiting: 1 }, NOW)).toBe('Working now')
  })
  it('opens the chat it has, or a new one addressed to it', () => {
    expect(teammateHref({ id: 't1', chat_id: 'abc' })).toBe('/chat/abc')
    expect(teammateHref({ id: 't 1', chat_id: null })).toBe('/chat?teammate=t%201')
  })
  it('is active on its own chat only', () => {
    const none = new URLSearchParams()
    const mate = { id: 't1', chat_id: 'abc' }
    expect(isTeammateActive(mate, ['chat', 'abc'], none)).toBe(true)
    expect(isTeammateActive(mate, ['chat', 'other'], none)).toBe(false)
    expect(isTeammateActive(mate, ['runs'], none)).toBe(false)
    const fresh = { id: 't2', chat_id: null }
    expect(isTeammateActive(fresh, ['chat'], new URLSearchParams('teammate=t2'))).toBe(true)
    expect(isTeammateActive(fresh, ['chat'], none)).toBe(false)
    // Addressed by id while it already has a chat: the server sends it there.
    expect(isTeammateActive(mate, ['chat'], new URLSearchParams('teammate=t1'))).toBe(true)
  })
})
