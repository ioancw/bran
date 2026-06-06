// Typed client for the /spa JSON API. The single place that talks to the backend.

import type {
  AgentInfo,
  Catalog,
  ChatEvent,
  ChatSummary,
  ProjectDetail,
  ProjectMemory,
  ProjectSummary,
  RunRecord,
  ScheduleRecord,
} from './types'

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} for ${url}`)
  return r.json() as Promise<T>
}

async function form<T>(url: string, fields: Record<string, string>, method = 'POST'): Promise<T> {
  const body = new URLSearchParams(fields)
  const r = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const api = {
  catalog: () => getJSON<Catalog>('/spa/catalog'),
  agents: () => getJSON<AgentInfo[]>('/spa/agents'),

  runs: (q: { agent?: string; status?: string; project_id?: string; schedule_id?: string; exclude_chats?: boolean; limit?: number } = {}) => {
    const p = new URLSearchParams()
    if (q.agent) p.set('agent', q.agent)
    if (q.status) p.set('status', q.status)
    if (q.project_id) p.set('project_id', q.project_id)
    if (q.schedule_id) p.set('schedule_id', q.schedule_id)
    if (q.exclude_chats) p.set('exclude_chats', 'true')
    if (q.limit) p.set('limit', String(q.limit))
    const qs = p.toString()
    return getJSON<RunRecord[]>(`/spa/runs${qs ? `?${qs}` : ''}`)
  },
  run: (id: string) => getJSON<RunRecord>(`/spa/runs/${encodeURIComponent(id)}`),
  runTranscript: (id: string) =>
    getJSON<{ run: RunRecord; events: ChatEvent[] }>(`/spa/runs/${encodeURIComponent(id)}/transcript`),
  newRun: (agent: string, task: string, opts: { project_id?: string; schedule_id?: string } = {}) => {
    const body: Record<string, string> = { agent, task }
    if (opts.project_id) body.project_id = opts.project_id
    if (opts.schedule_id) body.schedule_id = opts.schedule_id
    return form<RunRecord>('/spa/runs', body)
  },
  cancelRun: (id: string) =>
    form<{ run_id: string; cancelled: number }>(`/spa/runs/${encodeURIComponent(id)}/cancel`, {}),

  schedules: () => getJSON<ScheduleRecord[]>('/spa/schedules'),

  projects: () => getJSON<ProjectSummary[]>('/spa/projects'),
  projectDetail: (id: string) => getJSON<ProjectDetail>(`/spa/projects/${encodeURIComponent(id)}`),
  newProject: (name: string, description = '') =>
    form<ProjectSummary>('/spa/projects', { name, description }),
  saveProject: (id: string, fields: { name: string; description?: string; instructions?: string }) =>
    form<ProjectSummary>(`/spa/projects/${encodeURIComponent(id)}`, {
      name: fields.name, description: fields.description ?? '', instructions: fields.instructions ?? '',
    }),
  deleteProject: async (id: string): Promise<void> => {
    const r = await fetch(`/spa/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },
  memories: (projectId: string) =>
    getJSON<ProjectMemory[]>(`/spa/projects/${encodeURIComponent(projectId)}/memory`),
  addMemory: (projectId: string, text: string) =>
    form<ProjectMemory>(`/spa/projects/${encodeURIComponent(projectId)}/memory`, { text }),
  deleteMemory: async (projectId: string, entryId: string): Promise<void> => {
    const r = await fetch(
      `/spa/projects/${encodeURIComponent(projectId)}/memory/${encodeURIComponent(entryId)}`,
      { method: 'DELETE' },
    )
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },

  newSchedule: (fields: { name: string; agent: string; cron?: string; task?: string; project_id?: string; run_at?: string }) => {
    const body: Record<string, string> = {
      name: fields.name, agent: fields.agent, cron: fields.cron ?? '', task: fields.task ?? '',
    }
    if (fields.project_id) body.project_id = fields.project_id // omit → standalone
    if (fields.run_at) body.run_at = fields.run_at // present → one-shot
    return form<ScheduleRecord>('/spa/schedules', body)
  },
  deleteSchedule: async (name: string): Promise<void> => {
    const r = await fetch(`/spa/schedules/${encodeURIComponent(name)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },
  setScheduleEnabled: (name: string, enabled: boolean) =>
    form<ScheduleRecord>(`/spa/schedules/${encodeURIComponent(name)}/enabled`, {
      enabled: enabled ? 'true' : 'false',
    }),
  moveChat: (chatId: string, projectId: string) =>
    form<{ chat_id: string; project_id: string }>(
      `/spa/chats/${encodeURIComponent(chatId)}/move`, { project_id: projectId },
    ),

  chats: (projectId?: string | null) => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    return getJSON<ChatSummary[]>(`/spa/chats${qs}`)
  },
  chat: (id: string) => getJSON<ChatSummary>(`/spa/chats/${encodeURIComponent(id)}`),
  deleteChat: async (id: string): Promise<void> => {
    const r = await fetch(`/spa/chats/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },
  history: (id: string) =>
    getJSON<{ session_id: string; events: ChatEvent[] }>(`/spa/chats/${encodeURIComponent(id)}/history`),
}

/**
 * POST a prompt and yield unified ChatEvents as they stream in over SSE.
 * Parses the `data: {...}\n\n` frames; the terminal `{type:'done'}` ends it.
 */
export async function* streamChat(fields: Record<string, string>): AsyncGenerator<ChatEvent> {
  const body = new URLSearchParams()
  for (const [k, v] of Object.entries(fields)) if (v) body.set(k, v)

  const resp = await fetch('/spa/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      for (const line of rawEvent.split('\n')) {
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()
        if (!payload) continue
        try {
          yield JSON.parse(payload) as ChatEvent
        } catch {
          // ignore malformed frames
        }
      }
    }
  }
}
