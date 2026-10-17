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
  // `at` = ISO timestamp when the server knows it (replay from the transcript,
  // or the live stream's own clock). Absent on older transcripts.
  | { kind: 'user'; text: string; at?: string }
  | { kind: 'assistant'; text: string; at?: string }
  | { kind: 'thinking'; text: string }
  | { kind: 'routed'; agent: string }
  | { kind: 'footer'; numTurns: number | null; cost: number | null; stopped?: boolean }
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

/** Human-readable tool name for chat rows: strip the MCP plumbing prefix
 * ("mcp__bran_docs__fetch_url" → "fetch_url"). The raw name stays available
 * in the expanded params view. */
export function toolDisplayName(name: string): string {
  if (!name.startsWith('mcp__')) return name
  const parts = name.split('__')
  return parts.length >= 3 ? parts.slice(2).join('__') : name
}

function close(st: ReducerState): void {
  st.openAssistant = -1
}

/**
 * Apply one event to the items list (mutating in place so Svelte's deep
 * reactivity picks it up). Returns a session id when the event carries one, so
 * the caller can pivot the URL for a brand-new chat.
 */
export function applyEvent(items: ChatItem[], st: ReducerState, ev: ChatEvent): { session?: string } {
  switch (ev.type) {
    case 'session':
      return { session: ev.session_id }

    case 'text':
      if (st.openAssistant >= 0) {
        const cur = items[st.openAssistant] as Extract<ChatItem, { kind: 'assistant' }>
        cur.text += ev.text
      } else {
        const item: Extract<ChatItem, { kind: 'assistant' }> = { kind: 'assistant', text: ev.text }
        if (ev.ts) item.at = ev.ts
        items.push(item)
        st.openAssistant = items.length - 1
      }
      return {}

    case 'user': {
      close(st)
      const item: Extract<ChatItem, { kind: 'user' }> = { kind: 'user', text: ev.text }
      if (ev.ts) item.at = ev.ts
      items.push(item)
      return {}
    }

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

// --- Row grouping ------------------------------------------------------------
// A burst of tool calls (any names) folds into one expandable row so a fan-out
// of searches and reads stays scannable. Spawn and plan cards are never
// grouped — they carry their own UI — and they break a run.

export type Row =
  { group: false; item: ChatItem; idx: number } | { group: true; tools: ToolItem[]; idx: number }

export const SPAWN_TOOL = 'mcp__bran__spawn_agent'
export const PLAN_TOOL = 'mcp__bran__propose_plan'
const NEVER_GROUP = new Set([SPAWN_TOOL, PLAN_TOOL])
export const GROUP_MIN = 3

export function groupRows(items: ChatItem[]): Row[] {
  const out: Row[] = []
  let i = 0
  while (i < items.length) {
    const it = items[i]
    if (it.kind === 'tool' && !NEVER_GROUP.has(it.name)) {
      let j = i
      while (j < items.length && items[j].kind === 'tool' && !NEVER_GROUP.has((items[j] as ToolItem).name))
        j++
      const run = items.slice(i, j) as ToolItem[]
      if (run.length >= GROUP_MIN) {
        out.push({ group: true, tools: run, idx: i })
      } else {
        run.forEach((t, k) => out.push({ group: false, item: t, idx: i + k }))
      }
      i = j
    } else {
      out.push({ group: false, item: it, idx: i })
      i++
    }
  }
  return out
}

/** "5 tool calls · Read ×3, Grep ×2" — names ordered by frequency, then first appearance. */
export function groupLabel(tools: ToolItem[]): string {
  const counts = new Map<string, number>()
  for (const t of tools) {
    const n = toolDisplayName(t.name)
    counts.set(n, (counts.get(n) ?? 0) + 1)
  }
  const parts = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([n, c]) => (c > 1 ? `${n} ×${c}` : n))
  return `${tools.length} tool calls · ${parts.join(', ')}`
}
