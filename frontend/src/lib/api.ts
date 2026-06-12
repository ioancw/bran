// Typed client for the /spa JSON API. The single place that talks to the backend.

import type {
  AgentInfo,
  ArtifactEntry,
  Attachment,
  Catalog,
  ChatEvent,
  ChatSummary,
  ProjectDetail,
  ProjectFile,
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
  runArtifacts: (id: string) => getJSON<ArtifactEntry[]>(`/spa/runs/${encodeURIComponent(id)}/artifacts`),

  schedules: () => getJSON<ScheduleRecord[]>('/spa/schedules'),
  parseSchedule: (expr: string) =>
    getJSON<{ ok: boolean; cron: string; human: string; error: string }>(
      `/spa/schedules/parse?expr=${encodeURIComponent(expr)}`,
    ),

  projects: () => getJSON<ProjectSummary[]>('/spa/projects'),
  projectDetail: (id: string) => getJSON<ProjectDetail>(`/spa/projects/${encodeURIComponent(id)}`),
  newProject: (name: string, description = '') =>
    form<ProjectSummary>('/spa/projects', { name, description }),
  saveProject: (id: string, fields: { name: string; description?: string; instructions?: string; work_dir?: string }) =>
    form<ProjectSummary>(`/spa/projects/${encodeURIComponent(id)}`, {
      name: fields.name, description: fields.description ?? '', instructions: fields.instructions ?? '',
      work_dir: fields.work_dir ?? '',
    }),
  deleteProject: async (id: string): Promise<void> => {
    const r = await fetch(`/spa/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },
  addMemory: (projectId: string, text: string) =>
    form<ProjectMemory>(`/spa/projects/${encodeURIComponent(projectId)}/memory`, { text }),
  deleteMemory: async (projectId: string, entryId: string): Promise<void> => {
    const r = await fetch(
      `/spa/projects/${encodeURIComponent(projectId)}/memory/${encodeURIComponent(entryId)}`,
      { method: 'DELETE' },
    )
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },

  uploadFiles: async (projectId: string, files: FileList | File[]): Promise<ProjectFile[]> => {
    const body = new FormData()
    for (const f of Array.from(files)) body.append('files', f)
    const r = await fetch(`/spa/projects/${encodeURIComponent(projectId)}/files`, {
      method: 'POST',
      body, // browser sets multipart Content-Type + boundary
    })
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
    return r.json() as Promise<ProjectFile[]>
  },
  deleteFile: async (projectId: string, name: string): Promise<void> => {
    const r = await fetch(
      `/spa/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(name)}`,
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
  updateSchedule: (name: string, fields: { agent: string; task?: string; cron?: string; run_at?: string }) => {
    const body: Record<string, string> = {
      agent: fields.agent, task: fields.task ?? '', cron: fields.cron ?? '',
    }
    if (fields.run_at) body.run_at = fields.run_at // present → one-shot
    return form<ScheduleRecord>(`/spa/schedules/${encodeURIComponent(name)}`, body)
  },
  deleteSchedule: async (name: string): Promise<void> => {
    const r = await fetch(`/spa/schedules/${encodeURIComponent(name)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  },
  setScheduleEnabled: (name: string, enabled: boolean) =>
    form<ScheduleRecord>(`/spa/schedules/${encodeURIComponent(name)}/enabled`, {
      enabled: enabled ? 'true' : 'false',
    }),
  uploadAttachments: async (files: File[]): Promise<Attachment[]> => {
    const body = new FormData()
    for (const f of files) body.append('files', f)
    const r = await fetch('/spa/uploads', { method: 'POST', body })
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
    return r.json() as Promise<Attachment[]>
  },

  settings: () => getJSON<{ user_instructions: string }>('/spa/settings'),
  saveSettings: (fields: { user_instructions: string }) =>
    form<{ user_instructions: string }>('/spa/settings', fields),

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
  yield* readSSE(resp)
}

/**
 * Watch a background run execute: GET its live SSE stream (full replay first,
 * then live). Ends with {type:'done'} — immediately if the run already
 * finished, in which case the caller falls back to the stored transcript.
 */
export async function* streamRunEvents(runId: string, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  const resp = await fetch(`/spa/runs/${encodeURIComponent(runId)}/stream`, { signal })
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}: ${await resp.text()}`)
  }
  yield* readSSE(resp)
}

/** Parse `data: {...}\n\n` SSE frames off a streaming response body. */
async function* readSSE(resp: Response): AsyncGenerator<ChatEvent> {
  const reader = resp.body!.getReader()
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
