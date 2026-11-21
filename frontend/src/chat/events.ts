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
  at?: string // wall-clock ISO (from the event), when known
  endedAt?: string
  // The child run bran recorded for an Agent-tool delegation (server-attached).
  runId?: string | null
}

export type ChatItem =
  // `at` = ISO timestamp when the server knows it (replay from the transcript,
  // or the live stream's own clock). Absent on older transcripts.
  | { kind: 'user'; text: string; at?: string }
  | { kind: 'assistant'; text: string; at?: string }
  | { kind: 'thinking'; text: string; at?: string }
  | { kind: 'routed'; agent: string }
  | { kind: 'footer'; numTurns: number | null; cost: number | null; stopped?: boolean; at?: string }
  | { kind: 'error'; message: string }
  // A teammate's chat: a report it delivered on schedule, and a question it
  // asked from a scheduled run (the request itself is looked up by id).
  | { kind: 'delivery'; runId: string; title: string; excerpt: string; schedule: string | null; at: string }
  | { kind: 'request'; requestId: string; at: string }
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

/** True while the tool was pushed by the live stream moments ago — replayed
 * history is applied in one burst, so its client-side timing means nothing. */
function isLiveTool(tool: ToolItem): boolean {
  return performance.now() - tool.startedAt > 50
}

function close(st: ReducerState): void {
  st.openAssistant = -1
}

/** The turn is over, so nothing in it is still running. A tool call whose
 * result never arrived (a dropped event, a stopped turn) is closed here, with
 * no duration since nobody measured one; otherwise its row would spin and
 * count up forever beside a finished answer. */
function settleTools(items: ChatItem[], st: ReducerState): void {
  for (const idx of Object.values(st.tools)) {
    const it = items[idx]
    if (it?.kind === 'tool' && it.status === 'running') it.status = 'done'
  }
  st.tools = {}
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
      items.push({ kind: 'thinking', text: ev.text, at: ev.ts })
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
        runId: ev.run_id ?? null,
        at: ev.ts,
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
        tool.endedAt = ev.ts
        // Replayed history carries wall-clock stamps on both ends; a live
        // stream is timed on the client (performance.now) instead.
        const stamped = tool.at && ev.ts ? Date.parse(ev.ts) - Date.parse(tool.at) : NaN
        if (!Number.isNaN(stamped) && stamped >= 0 && !isLiveTool(tool)) tool.durationMs = stamped
        else {
          const d = performance.now() - tool.startedAt
          tool.durationMs = d > 50 ? d : null
        }
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
      settleTools(items, st)
      items.push({ kind: 'footer', numTurns: ev.num_turns, cost: ev.total_cost_usd, at: ev.ts })
      return {}

    case 'error':
      close(st)
      settleTools(items, st)
      items.push({ kind: 'error', message: ev.message })
      return {}

    case 'done':
      close(st)
      settleTools(items, st)
      return {}

    case 'delivery':
      close(st)
      items.push({
        kind: 'delivery',
        runId: ev.run_id,
        title: ev.title,
        excerpt: ev.excerpt,
        schedule: ev.schedule,
        at: ev.ts,
      })
      return {}

    case 'request':
      close(st)
      items.push({ kind: 'request', requestId: ev.request_id, at: ev.ts })
      return {}
  }
}

/** The feed events (a teammate's deliveries and questions) not in `items`
 * yet: what a refresh adds at the end of the conversation. */
export function unseenFeed(items: ChatItem[], feed: ChatEvent[]): ChatEvent[] {
  const have = new Set<string>()
  for (const it of items) {
    if (it.kind === 'delivery') have.add('d:' + it.runId)
    else if (it.kind === 'request') have.add('r:' + it.requestId)
  }
  return feed.filter(
    (ev) =>
      (ev.type === 'delivery' && !have.has('d:' + ev.run_id)) ||
      (ev.type === 'request' && !have.has('r:' + ev.request_id)),
  )
}

/** The id of the request an ask_approval call made, from its result. */
export function askedRequestId(tool: ToolItem): string | null {
  return tool.resultText.match(/request_id=([0-9a-f-]{8,})/i)?.[1] ?? null
}

// --- Row grouping ------------------------------------------------------------
// The agent's work between two pieces of prose — its thinking and its tool
// calls, however many — folds into ONE activity row: a live line while it
// happens, a one-line summary afterwards, the full trace on demand. Cards
// that carry their own UI (a spawn, a plan, a delegation, a saved document)
// are never folded, and they break a run.

export type WorkItem = ToolItem | Extract<ChatItem, { kind: 'thinking' }>

export type Row =
  | { group: false; item: ChatItem; idx: number }
  // `steps` is the whole run in order; `tools` is its tool calls only.
  | { group: true; tools: ToolItem[]; steps: WorkItem[]; idx: number }

export const SPAWN_TOOL = 'mcp__bran__spawn_agent'
export const PLAN_TOOL = 'mcp__bran__propose_plan'
// Writes a document into the project's files — shown as a card that opens it.
export const DOC_TOOL = 'mcp__bran_docs__save_document'
// A teammate asking before it changes its job — shown as the question itself.
export const ASK_TOOL = 'mcp__bran_team__ask_approval'
// The SDK's delegation tool ("Task" before Claude Code 2.1.63). Each call is
// its own card, never folded into a tool burst.
export const DELEGATION_TOOLS = new Set(['Agent', 'Task', 'Workflow'])
export const isDelegation = (name: string) => DELEGATION_TOOLS.has(name)
const NEVER_GROUP = new Set([SPAWN_TOOL, PLAN_TOOL, DOC_TOOL, ASK_TOOL, ...DELEGATION_TOOLS])

/** Thinking, or a tool call without a card of its own: what an activity
 * row is made of. */
export function isWork(it: ChatItem): it is WorkItem {
  return it.kind === 'thinking' || (it.kind === 'tool' && !NEVER_GROUP.has(it.name))
}

export function groupRows(items: ChatItem[]): Row[] {
  const out: Row[] = []
  let i = 0
  while (i < items.length) {
    const it = items[i]
    if (isWork(it)) {
      let j = i
      while (j < items.length && isWork(items[j])) j++
      const steps = items.slice(i, j) as WorkItem[]
      const tools = steps.filter((s): s is ToolItem => s.kind === 'tool')
      out.push({ group: true, tools, steps, idx: i })
      i = j
    } else {
      out.push({ group: false, item: it, idx: i })
      i++
    }
  }
  return out
}
