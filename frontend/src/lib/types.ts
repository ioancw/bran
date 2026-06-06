// Wire types for the /spa API. Kept hand-written and small; `npm run gen:types`
// can regenerate a full api-types.ts from FastAPI's OpenAPI when needed.

export interface AgentInfo {
  name: string
  description: string
  model: string | null
  tools: string[]
  subagents: string[]
}

export interface CatalogItem {
  name: string
  description: string
}

export interface Catalog {
  agents: CatalogItem[]
  commands: CatalogItem[]
}

export interface RunRecord {
  id: string
  agent: string
  task: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  session_id: string | null
  parent_run_id: string | null
  result: string | null
  error: string | null
  total_cost_usd: number | null
  num_turns: number | null
  duration_ms: number | null
  started_at: string
  ended_at: string | null
  metadata: Record<string, unknown>
  project_id: string | null // null = standalone (not attached to a project)
  source: 'chat' | 'runner' | 'spawn' | 'manual' // how the run was triggered
}

export interface ScheduleRecord {
  id: string
  name: string
  agent: string
  task: string
  cron: string
  enabled: boolean
  created_at: string
  project_id: string | null // null = standalone Runner
}

export interface ChatSummary {
  id: string
  title: string
  agent: string
  project_id: string | null // null = loose chat (no project)
  updated_at: string
  created_at: string
}

export interface ProjectSummary {
  id: string
  name: string
  description: string
  instructions: string
  n_chats: number
  updated_at: string
}

export interface BriefingSummary {
  name: string
  size_kb: number
  mtime: number
}

export interface ProjectMemory {
  id: string
  project_id: string
  text: string
  created_at: string
}

// The project "workspace hub": a project plus everything that flows through it.
export interface ProjectDetail {
  project: ProjectSummary
  chats: ChatSummary[]
  memories: ProjectMemory[]
  schedules: ScheduleRecord[]
  runs: RunRecord[]
}

export interface DashboardData {
  stats: {
    runs_completed: number
    runs_failed: number
    runs_running: number
    total_cost_usd: number
    per_agent: { name: string; count: number }[]
  }
  upcoming: { name: string; agent: string; cron: string; next_run: string | null; countdown: string }[]
  buckets: {
    label: string
    items: { kind: string; title: string; snippet: string; agent: string; time_label: string; href: string }[]
  }[]
  today_label: string
}

// --- The unified chat event (mirror of bran.web.events). One shape for both
// the live stream and replayed history. ---
export type ChatEvent =
  | { type: 'session'; session_id: string }
  | { type: 'routed'; agent: string }
  | { type: 'user'; text: string }
  | { type: 'text'; text: string }
  | { type: 'thinking'; text: string }
  | { type: 'tool_use'; name: string; input: Record<string, unknown>; tool_id: string | null }
  | { type: 'tool_result'; tool_id: string | null; text: string; is_error: boolean }
  | { type: 'result'; num_turns: number | null; total_cost_usd: number | null; session_id: string | null }
  | { type: 'error'; message: string }
  | { type: 'done' }
