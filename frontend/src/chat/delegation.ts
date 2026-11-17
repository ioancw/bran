// How handing work to another agent reads in a run's timeline: the three ways
// a run does it (an inline subagent, a saved workflow, a background run), and
// the notice the SDK sends when background work finishes.

const SPAWN = 'mcp__bran__spawn_agent'

/** The row's label and detail for a delegation call. `childAgent` is the
 * agent of the child run bran recorded for it, when known. */
export function delegationLabel(
  name: string,
  input: Record<string, unknown>,
  childAgent?: string,
): { label: string; detail: string } {
  const text = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
  if (name === 'Workflow') {
    const wf = text(input.name) || (childAgent ?? '').replace(/^workflow:/, '') || 'a workflow'
    const args = input.args && typeof input.args === 'object' ? (input.args as Record<string, unknown>) : {}
    return { label: `Started workflow ${wf}`, detail: text(args.path) || text(input.description) }
  }
  if (name === SPAWN) {
    const agent = text(input.agent) || childAgent || 'an agent'
    return { label: `Started background run: ${agent}`, detail: text(input.task) || text(input.prompt) }
  }
  const agent = text(input.subagent_type) || childAgent || 'general-purpose'
  return { label: `Delegated to ${agent}`, detail: text(input.description) || text(input.prompt) }
}

export interface TaskNotification {
  toolUseId: string | null
  status: string
  summary: string
}

/** The SDK's "a background task finished" message, which arrives in the
 * transcript as a user turn. Null for anything a person actually wrote. */
export function parseTaskNotification(text: string): TaskNotification | null {
  if (!text.trimStart().startsWith('<task-notification>')) return null
  const tag = (name: string) => {
    const m = text.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`))
    return m ? m[1].trim() : ''
  }
  return {
    toolUseId: tag('tool-use-id') || null,
    status: tag('status') || 'completed',
    summary: tag('summary') || 'A background task finished',
  }
}
