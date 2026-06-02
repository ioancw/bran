// Folds the unified ChatEvent stream into a flat list of render items. Works
// identically for the live stream and for replayed history — that's the whole
// point of the single event schema. Pure logic (no runes); the Chat component
// owns the reactive `items` array and mutates it through these helpers.

import type { ChatEvent } from '../lib/types'

export interface ToolItem {
  kind: 'tool'
  toolId: string | null
  name: string
  input: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  resultText: string
  isError: boolean
  startedAt: number
  durationMs: number | null
}

export type ChatItem =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string }
  | { kind: 'thinking'; text: string }
  | { kind: 'routed'; agent: string }
  | { kind: 'footer'; numTurns: number | null; cost: number | null }
  | { kind: 'error'; message: string }
  | ToolItem

export interface ReducerState {
  // Index of the current appendable assistant bubble, or -1 if none is open.
  openAssistant: number
  // tool_id -> index into items, so a tool_result can find its call.
  tools: Record<string, number>
}

export function freshState(): ReducerState {
  return { openAssistant: -1, tools: {} }
}

function close(st: ReducerState): void {
  st.openAssistant = -1
}

/**
 * Apply one event to the items list (mutating in place so Svelte's deep
 * reactivity picks it up). Returns a session id when the event carries one, so
 * the caller can pivot the URL for a brand-new chat.
 */
export function applyEvent(
  items: ChatItem[],
  st: ReducerState,
  ev: ChatEvent,
): { session?: string } {
  switch (ev.type) {
    case 'session':
      return { session: ev.session_id }

    case 'text':
      if (st.openAssistant >= 0) {
        const cur = items[st.openAssistant] as Extract<ChatItem, { kind: 'assistant' }>
        cur.text += ev.text
      } else {
        items.push({ kind: 'assistant', text: ev.text })
        st.openAssistant = items.length - 1
      }
      return {}

    case 'user':
      close(st)
      items.push({ kind: 'user', text: ev.text })
      return {}

    case 'thinking':
      close(st)
      items.push({ kind: 'thinking', text: ev.text })
      return {}

    case 'routed':
      close(st)
      items.push({ kind: 'routed', agent: ev.agent })
      return {}

    case 'tool_use': {
      close(st)
      const tool: ToolItem = {
        kind: 'tool',
        toolId: ev.tool_id,
        name: ev.name,
        input: ev.input,
        status: 'running',
        resultText: '',
        isError: false,
        startedAt: performance.now(),
        durationMs: null,
      }
      items.push(tool)
      if (ev.tool_id) st.tools[ev.tool_id] = items.length - 1
      return {}
    }

    case 'tool_result': {
      close(st)
      const idx = ev.tool_id != null ? st.tools[ev.tool_id] : undefined
      if (idx != null && items[idx]?.kind === 'tool') {
        const tool = items[idx] as ToolItem
        tool.status = ev.is_error ? 'error' : 'done'
        tool.isError = ev.is_error
        tool.resultText = ev.text
        const d = performance.now() - tool.startedAt
        tool.durationMs = d > 50 ? d : null
      } else {
        // Orphan result (no matching call) — surface it standalone.
        items.push({
          kind: 'tool',
          toolId: ev.tool_id,
          name: 'result',
          input: {},
          status: ev.is_error ? 'error' : 'done',
          resultText: ev.text,
          isError: ev.is_error,
          startedAt: performance.now(),
          durationMs: null,
        })
      }
      return {}
    }

    case 'result':
      close(st)
      items.push({ kind: 'footer', numTurns: ev.num_turns, cost: ev.total_cost_usd })
      return {}

    case 'error':
      close(st)
      items.push({ kind: 'error', message: ev.message })
      return {}

    case 'done':
      close(st)
      return {}
  }
}
