// A teammate is an agent with a name, a standing brief, one chat and its own
// automations (bran.teammates on the server). This is how one reads in the
// sidebar, and where clicking it goes.

import type { Teammate } from './types'
import { countdown } from './time'

/** One short line beside the name: what it is doing, or when it is next due.
 *  Short enough for a narrow rail — the dot says the rest. */
export function teammateHint(t: Pick<Teammate, 'state' | 'next_run'>, nowMs: number = Date.now()): string {
  if (t.state === 'working') return 'Working now'
  if (t.state === 'failed') return 'failed'
  if (t.state === 'paused') return 'Paused'
  return t.next_run ? countdown(t.next_run, nowMs) : ''
}

/** A teammate IS its chat: the one it already has, or a new one addressed to
 *  it (the server ties the session to the teammate on the first message). */
export function teammateHref(t: Pick<Teammate, 'id' | 'chat_id'>): string {
  return t.chat_id ? '/chat/' + encodeURIComponent(t.chat_id) : '/chat?teammate=' + encodeURIComponent(t.id)
}

/** Whether the route is showing this teammate's chat. */
export function isTeammateActive(
  t: Pick<Teammate, 'id' | 'chat_id'>,
  segments: string[],
  query: URLSearchParams,
): boolean {
  if (segments[0] !== 'chat') return false
  if (segments[1]) return !!t.chat_id && segments[1] === t.chat_id
  return query.get('teammate') === t.id
}
