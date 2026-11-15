// The words for an agent's work: what a step is doing right now ("Reading
// bbc.co.uk"), what a finished burst of steps amounted to ("3 web searches ·
// 4 pages read"), and what a whole turn cost in time and help ("Worked for
// 42s · 6 sources · 3 agents"). Pure logic — the components only render it.

import { isDelegation, SPAWN_TOOL, toolDisplayName, type ChatItem, type ToolItem } from './events'
import { domainOf, FETCH_TOOLS } from './sources'
import { shortPath, truncate } from './tool-labels'

export type Step = ToolItem | Extract<ChatItem, { kind: 'thinking' }>

type Kind = 'websearch' | 'page' | 'file' | 'filesearch' | 'write' | 'shell' | 'other'

function kindOf(name: string): Kind {
  const n = name.toLowerCase()
  if (n === 'websearch' || n === 'mcp__tavily__tavily_search') return 'websearch'
  if (FETCH_TOOLS.has(name) || n === 'webfetch') return 'page'
  if (n === 'read' || n.endsWith('__read_pdf')) return 'file'
  if (n === 'grep' || n === 'glob' || n === 'ls') return 'filesearch'
  if (['edit', 'write', 'multiedit', 'notebookedit'].includes(n)) return 'write'
  if (n === 'bash' || n === 'powershell') return 'shell'
  if (n.includes('search')) return 'websearch'
  if (n.includes('fetch')) return 'page'
  return 'other'
}

function firstUrl(input: Record<string, unknown>): string {
  const raw = input?.url ?? input?.urls
  const s = Array.isArray(raw) ? String(raw[0] ?? '') : typeof raw === 'string' ? raw : ''
  return s.split(/[,\s]+/)[0] ?? ''
}

/** What a step is doing, as a present-tense line for the live indicator. */
export function stepLabel(step: Step): string {
  if (step.kind === 'thinking') return 'Thinking'
  const i = step.input || {}
  switch (kindOf(step.name)) {
    case 'websearch':
      return i.query ? `Searching the web for “${truncate(i.query, 60)}”` : 'Searching the web'
    case 'page': {
      const d = domainOf(firstUrl(i))
      return d ? `Reading ${d}` : 'Reading a page'
    }
    case 'file':
      return i.file_path || i.path ? `Reading ${shortPath(i.file_path ?? i.path)}` : 'Reading a file'
    case 'filesearch':
      return i.pattern ? `Searching files for “${truncate(i.pattern, 40)}”` : 'Looking through files'
    case 'write':
      return i.file_path ? `Writing ${shortPath(i.file_path)}` : 'Writing a file'
    case 'shell':
      return 'Running a command'
    default:
      return `Using ${toolDisplayName(step.name)}`
  }
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** What a finished burst of steps amounted to. */
export function activitySummary(steps: Step[]): string {
  const n: Record<Kind, number> = {
    websearch: 0,
    page: 0,
    file: 0,
    filesearch: 0,
    write: 0,
    shell: 0,
    other: 0,
  }
  let failed = 0
  let tools = 0
  for (const s of steps) {
    if (s.kind !== 'tool') continue
    tools++
    n[kindOf(s.name)]++
    if (s.isError) failed++
  }
  if (!tools) return 'Thought it through'
  const parts: string[] = []
  if (n.websearch) parts.push(plural(n.websearch, 'web search', 'web searches'))
  if (n.page) parts.push(`${plural(n.page, 'page', 'pages')} read`)
  if (n.file) parts.push(`${plural(n.file, 'file', 'files')} read`)
  if (n.filesearch) parts.push(plural(n.filesearch, 'file search', 'file searches'))
  if (n.write) parts.push(`${plural(n.write, 'file', 'files')} written`)
  if (n.shell) parts.push(plural(n.shell, 'command', 'commands'))
  if (n.other)
    parts.push(plural(n.other, parts.length ? 'other step' : 'step', parts.length ? 'other steps' : 'steps'))
  if (failed) parts.push(`${failed} failed`)
  return parts.join(' · ')
}

/** "8s", "42s", "1m 05s", "1h 02m". */
export function fmtSpan(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${Math.max(1, s)}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`
}

/** Time the steps took, when their durations are known. */
export function activityDuration(steps: Step[]): number | null {
  let total = 0
  let known = false
  for (const s of steps) {
    if (s.kind === 'tool' && s.durationMs != null) {
      total += s.durationMs
      known = true
    }
  }
  return known ? total : null
}

function stamp(iso: string | undefined): number {
  if (!iso) return NaN
  return Date.parse(iso)
}

/** How long items[start, end) took (first timestamp to last) and how many
 * agents were handed work. Null duration when the timestamps do not say. */
export function turnStats(
  items: ChatItem[],
  start: number,
  end: number,
): { durationMs: number | null; agents: number } {
  let first = NaN
  let last = NaN
  let agents = 0
  for (let i = start; i < end && i < items.length; i++) {
    const it = items[i]
    const stamps: number[] = []
    if (it.kind === 'tool') {
      stamps.push(stamp(it.at), stamp(it.endedAt))
      if (isDelegation(it.name) || it.name === SPAWN_TOOL) agents++
    } else if (
      it.kind === 'user' ||
      it.kind === 'assistant' ||
      it.kind === 'thinking' ||
      it.kind === 'footer'
    ) {
      stamps.push(stamp(it.at))
    }
    for (const t of stamps) {
      if (Number.isNaN(t)) continue
      if (Number.isNaN(first) || t < first) first = t
      if (Number.isNaN(last) || t > last) last = t
    }
  }
  const span = last - first
  return { durationMs: Number.isNaN(span) || span < 1000 ? null : span, agents }
}

/** The turn's closing line: "Worked for 42s · 6 sources · 3 agents · $0.0123". */
export function turnSummary(
  stats: { durationMs: number | null; agents: number },
  nSources: number,
  cost: number | null,
): string {
  const parts: string[] = []
  parts.push(stats.durationMs != null ? `Worked for ${fmtSpan(stats.durationMs)}` : 'Done')
  if (nSources) parts.push(plural(nSources, 'source', 'sources'))
  if (stats.agents) parts.push(plural(stats.agents, 'agent', 'agents'))
  if (cost != null) parts.push(`$${cost.toFixed(4)}`)
  return parts.join(' · ')
}
