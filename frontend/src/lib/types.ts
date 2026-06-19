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
  schedule_id: string | null // the runner this run belongs to, if any
  actor?: string | null // named API token that triggered the run, if any
  artifacts?: string[] // file paths the run produced (list endpoints only)
  starred?: boolean // you starred this output (list endpoints only)
  read_at?: string | null // when you last read it; null/absent = unread
}

// Evaluator verdict stored in run.metadata.verification for verify-mode runners.
export interface Verification {
  status: 'done' | 'skipped'
  pass?: boolean
  issues?: string[]
  feedback?: string
  reason?: string
  retried_run_id?: string
  retry_of?: string
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
  run_at?: string | null // one-shot fire time; null/absent = recurring (cron)
  next_run?: string | null // computed next fire time (null when paused/invalid)
  verify?: boolean // evaluator reviews each output; failed verdicts re-run once
  delta?: boolean // each run sees the previous report and reports only changes
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
  work_dir: string // optional real folder agents may read AND write ('' = none)
  n_chats: number
  updated_at: string
}

// A chat-composer attachment, saved server-side under bran_home/uploads and
// referenced by absolute path in the prompt.
export interface Attachment {
  name: string
  path: string
  size_human: string
}

// A file a run produced (captured from its Write/Edit tool calls).
export interface ArtifactEntry {
  index: number
  name: string
  path: string
  exists: boolean
  size: number | null
}

export interface ProjectMemory {
  id: string
  project_id: string
  text: string
  created_at: string
}

// A working file uploaded into a project's folder.
export interface ProjectFile {
  name: string
  size: number
  size_human: string
  modified: number
  path: string
}

// The project "workspace hub": a project plus everything that flows through it.
export interface ProjectDetail {
  project: ProjectSummary
  chats: ChatSummary[]
  memories: ProjectMemory[]
  files: ProjectFile[]
  schedules: ScheduleRecord[]
  runs: RunRecord[]
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
