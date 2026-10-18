// How a tool call reads to a person: its category (for colour and icon), a
// one-line summary of what it was asked, and a one-word preview of what came
// back. Shared by the chat's tool rows and the run timeline.
import { shellLooksFailed } from './format'

export type ToolCategory = 'search' | 'write' | 'shell' | 'web' | 'agent' | 'misc'

function lower(s: string): string {
  return (s || '').toLowerCase()
}

export function category(name: string): ToolCategory {
  const n = lower(name)
  if (['read', 'glob', 'grep', 'ls'].includes(n)) return 'search'
  if (['edit', 'write', 'multiedit', 'notebookedit'].includes(n)) return 'write'
  if (['bash', 'powershell'].includes(n)) return 'shell'
  if (n.startsWith('web') || n.startsWith('mcp__tavily')) return 'web'
  if (n === 'agent' || n === 'task' || n.startsWith('mcp__bran__spawn')) return 'agent'
  if (n.includes('fetch') || n.includes('search')) return 'web'
  return 'misc'
}

/** Icon name (components/Icon.svelte) for a tool category. */
export function categoryIcon(cat: ToolCategory): string {
  switch (cat) {
    case 'search':
      return 'search'
    case 'write':
      return 'pencil'
    case 'shell':
      return 'zap'
    case 'web':
      return 'link'
    case 'agent':
      return 'bot'
    default:
      return 'zap'
  }
}

export function shortPath(p: unknown): string {
  if (!p) return ''
  const parts = String(p).replace(/\\/g, '/').split('/').filter(Boolean)
  return parts.length <= 3 ? parts.join('/') : '…/' + parts.slice(-2).join('/')
}

export function truncate(s: unknown, max: number): string {
  const str = String(s ?? '')
  return str.length > max ? str.slice(0, max - 1) + '…' : str
}

export function summary(name: string, input: Record<string, unknown>): string {
  const n = lower(name)
  const i = input || {}
  if (['read', 'edit', 'write'].includes(n)) return i.file_path ? shortPath(i.file_path) : ''
  if (['bash', 'powershell'].includes(n)) return i.command ? truncate(i.command, 60) : ''
  if (n === 'grep' || n === 'glob') return i.pattern ? truncate(i.pattern, 40) : ''
  if (n === 'agent' || n === 'task') {
    const sub = (i.subagent_type as string) || 'subagent'
    const desc = i.description ? ` — ${truncate(i.description, 40)}` : ''
    return `${sub}${desc}`
  }
  if (n === 'websearch' || n === 'mcp__tavily__tavily_search') return i.query ? truncate(i.query, 60) : ''
  if (n === 'webfetch' || n === 'mcp__tavily__tavily_extract') return i.url ? truncate(i.url, 60) : ''
  // Unknown tools (MCP etc.): surface the most informative string param, so
  // "fetch_url  https://feeds.bbci.co.uk/…" instead of a bare name.
  for (const k of [
    'url',
    'query',
    'file_path',
    'path',
    'pattern',
    'command',
    'task',
    'name',
    'prompt',
    'text',
  ]) {
    const v = i[k]
    if (typeof v === 'string' && v.trim()) return truncate(v, 60)
  }
  return ''
}

export function preview(name: string, text: string, isError: boolean): string {
  if (isError) return 'error'
  if (!text) return ''
  const n = lower(name)
  if (n === 'read') return `${text.split('\n').length} lines`
  if (n === 'grep') return `${text.split('\n').filter(Boolean).length} matches`
  if (n === 'glob') return `${text.split('\n').filter(Boolean).length} files`
  if (n === 'bash' || n === 'powershell') return shellLooksFailed(text) ? 'with errors' : 'ok'
  if (n === 'edit' || n === 'write') return 'saved'
  if (n === 'agent' || n === 'task') return 'done'
  return 'ok'
}
