// The rules of a chat turn, apart from the component that renders it: what
// goes into the transcript when a turn starts, stops or fails; what a Retry
// re-sends; how replayed history replaces the view; and where drafts live.
// Pure logic (no runes) so it can be unit-tested — Chat.svelte owns the
// reactive state and calls in here.

import type { ChatEvent } from '../lib/types'
import { applyEvent, freshState, type ChatItem, type ReducerState } from './events'

/** Start a turn: the user's bubble goes in, and any assistant bubble still
 * open from the previous turn is closed. (Only reducer events used to close
 * it, so after a Stop or a dropped stream the next reply's text was appended
 * to the old bubble, above the new prompt.) */
export function beginTurn(items: ChatItem[], st: ReducerState, text: string): void {
  st.openAssistant = -1
  items.push({ kind: 'user', text, at: new Date().toISOString() })
}

/** The user pressed stop: a quiet note, and nothing stays open. */
export function endTurnStopped(items: ChatItem[], st: ReducerState): void {
  st.openAssistant = -1
  items.push({ kind: 'footer', numTurns: null, cost: null, stopped: true })
}

/** The turn failed before or while streaming. */
export function failTurn(items: ChatItem[], st: ReducerState, message: string): void {
  st.openAssistant = -1
  items.push({ kind: 'error', message })
}

/** True when the transcript ends in an error row (the only place Retry shows). */
export function endsInError(items: ChatItem[]): boolean {
  return items.length > 0 && items[items.length - 1].kind === 'error'
}

/**
 * Prepare a retry of the turn that just failed and return the prompt to
 * re-send — the text of the last user message, whatever failed (the network,
 * or an error the server reported) and wherever the transcript came from (the
 * live stream or replayed history). Removes the trailing error row, and the
 * user bubble too when nothing was answered after it (the re-send adds it
 * back). Returns null — and changes nothing — when there is nothing to retry.
 */
export function takeRetry(items: ChatItem[]): string | null {
  if (!endsInError(items)) return null
  let i = items.length - 2
  while (i >= 0 && items[i].kind !== 'user') i--
  if (i < 0) return null
  const text = (items[i] as Extract<ChatItem, { kind: 'user' }>).text
  items.pop() // the error row
  if (i === items.length - 1) items.pop() // unanswered prompt
  return text
}

/** Replay a conversation's events into a fresh item list. The returned state
 * is closed: a live turn started afterwards must begin its own bubble. */
export function itemsFromHistory(events: ChatEvent[]): { items: ChatItem[]; state: ReducerState } {
  const items: ChatItem[] = []
  const st = freshState()
  for (const ev of events) applyEvent(items, st, ev)
  return { items, state: freshState() }
}

// --- per-chat drafts ---------------------------------------------------------
// A half-typed message survives navigation and never leaks into another
// conversation: each chat (and "new") keeps its own draft for the session.

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export const draftKey = (sid: string | null) => 'bran.draft:' + (sid ?? 'new')

export function readDraft(sid: string | null, store: Store = sessionStorage): string {
  try {
    return store.getItem(draftKey(sid)) ?? ''
  } catch {
    return ''
  }
}

export function writeDraft(sid: string | null, text: string, store: Store = sessionStorage): void {
  try {
    if (text.trim()) store.setItem(draftKey(sid), text)
    else store.removeItem(draftKey(sid))
  } catch {
    /* storage unavailable — drafts just don't persist */
  }
}

/** The run ids of this conversation's background spawns (fan-out tracking). */
export function spawnRunIds(items: ChatItem[]): string[] {
  const ids: string[] = []
  for (const it of items) {
    if (it.kind === 'tool' && it.name === 'mcp__bran__spawn_agent') {
      const m = it.resultText?.match(/run_id=([0-9a-f-]{8,})/i)
      if (m) ids.push(m[1])
    }
  }
  return ids
}
