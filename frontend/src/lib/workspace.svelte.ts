// Shared workspace state. Cowork keeps the conversation list (Recents) in the
// global left rail, so the chat list can no longer live inside the Chat page —
// the Sidebar (Recents) and Chat (breadcrumb switcher) both read it from here.
//
// `scopeProjectId` is the project whose conversations are shown (null = loose /
// global Recents). Pages set the scope as the route resolves; everything that
// renders Recents reacts to this one store.

import { api } from './api'
import type { ChatSummary, ProjectSummary } from './types'

export const workspace = $state<{
  scopeProjectId: string | null
  chats: ChatSummary[]
  projects: ProjectSummary[]
  activityTick: number // bumped when a run/chat completes, so the rail's Progress reloads
  streaming: boolean // a chat in the current scope is actively streaming
  liveLabel: string // what it's doing right now (thinking / using a tool / responding)
}>({
  scopeProjectId: null,
  chats: [],
  projects: [],
  activityTick: 0,
  streaming: false,
  liveLabel: '',
})

/** Signal that background activity changed (e.g. a chat turn finished). */
export function bumpActivity(): void {
  workspace.activityTick++
}

/** Reflect live chat activity into the rail's Progress panel. */
export function setLive(streaming: boolean, label = ''): void {
  workspace.streaming = streaming
  workspace.liveLabel = label
}

/** Reload the conversation list for the current scope (null = loose). */
export async function loadChats(): Promise<void> {
  // Capture the scope at call time: a rapid scope hop (A→B) could otherwise let
  // A's slower response land last and show A's chats under scope B.
  const scope = workspace.scopeProjectId
  try {
    const chats = await api.chats(scope)
    if (scope === workspace.scopeProjectId) workspace.chats = chats
  } catch {
    /* leave the previous list in place */
  }
}

/** Reload the project list (used for names + the new-runner attach picker). */
export async function loadProjects(): Promise<void> {
  try {
    workspace.projects = await api.projects()
  } catch {
    /* ignore */
  }
}

/** Switch which project's conversations Recents shows. Reloads on change. */
export function setScope(id: string | null): void {
  if (workspace.scopeProjectId === id) return
  workspace.scopeProjectId = id
  void loadChats()
}

/** Map a project id to its display name (falls back to the id). */
export function projectName(id: string | null): string | null {
  if (!id) return null
  return workspace.projects.find((p) => p.id === id)?.name ?? id
}
