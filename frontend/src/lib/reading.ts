// Helpers for the reading view of a report (pages/Reader.svelte).

// How far into a report its title may sit: agents often open with a line or
// two ("Briefing saved to …. Here is the full report:") before the heading.
const HEADLINE_WITHIN = 600

/** A report's top-level heading ("# Morning brief") reads better as the page's
 * headline. It is lifted out when it opens the report or follows a short
 * preamble; the preamble and everything after the heading stay as the body. */
export function splitHeadline(markdown: string): { headline: string | null; body: string } {
  const text = markdown.replace(/^\s+/, '')
  const m = /^#[ \t]+(.+?)[ \t]*#*[ \t]*$/m.exec(text)
  if (!m || m.index > HEADLINE_WITHIN) return { headline: null, body: markdown }
  // A "#" line inside a code fence is a comment, not a heading.
  const fences = text.slice(0, m.index).match(/^(```|~~~)/gm)
  if (fences && fences.length % 2 === 1) return { headline: null, body: markdown }
  const headline = m[1].replace(/[*_`]/g, '').trim()
  if (!headline) return { headline: null, body: markdown }
  const before = text.slice(0, m.index).replace(/\s+$/, '')
  const after = text.slice(m.index + m[0].length).replace(/^\s+/, '')
  // A rule the agent drew between its preamble and the title is now a rule
  // above nothing — drop it.
  const preamble = before.replace(/(^|\n)\s*(-{3,}|\*{3,}|_{3,})\s*$/, '').replace(/\s+$/, '')
  return { headline, body: preamble ? `${preamble}\n\n${after}` : after }
}

/** The first words of a report, for a card: its own opening, as a sentence.
 * Not the agent's covering note above the title ("Fact-check done, saved at
 * …"), and not headings, label lines ("**Lens:** FX"), quoted notes, tables or
 * code. List items count, without their markers. */
export function excerpt(markdown: string, limit = 220): string {
  const text = markdown.replace(/^\s+/, '')
  const m = /^#[ \t]+.+$/m.exec(text)
  const body = m && m.index <= HEADLINE_WITHIN ? text.slice(m.index + m[0].length) : text
  const parts: string[] = []
  let fenced = false
  for (const raw of body.split('\n')) {
    const line = raw.trim()
    if (/^(```|~~~)/.test(line)) fenced = !fenced
    if (fenced || !line || /^([#|>`~]|-{3,}|\*{3,}|_{3,})/.test(line) || /^\*\*[^*]+:\*\*/.test(line))
      continue
    parts.push(line.replace(/^([-*+]|\d+[.)])\s+/, ''))
    if (parts.join(' ').length > limit + 20) break
  }
  let s = parts
    .join(' ')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
  if (s.length > limit) s = s.slice(0, limit - 1).replace(/\s+\S*$/, '') + '…'
  return s || text.slice(0, limit)
}

/** Where a move from `current` to `to` came from. Stepping between editions of
 * a report in the reading view is still the same visit: it keeps the page the
 * reading began from. */
export function originOf(current: string, currentFrom: string | null, to: string): string | null {
  const reading = (p: string) => p.startsWith('/read/')
  return reading(current) && reading(to) ? currentFrom : current
}

/** Where the reading view's back link goes and what it says: the page the
 * reader came from, or Outputs when the report was opened directly (from a
 * notification, say). `chatName` names the chat when that is known. */
export function backLink(from: string | null, chatName = ''): { to: string; label: string } {
  const path = (from ?? '').split('?')[0]
  const seg = path.split('/').filter(Boolean)
  const named: Record<string, [string, string]> = {
    runs: ['Back to Runs', 'Back to the run'],
    chat: ['Back to the chat', `Back to ${chatName || 'the chat'}`],
    runners: ['Back to Automations', 'Back to the automation'],
    projects: ['Back to Projects', 'Back to the project'],
    agents: ['Back to the agent library', 'Back to the agent'],
    teammates: ['Back to Teammates', 'Back to Teammates'],
    artifacts: ['Back to Artifacts', 'Back to Artifacts'],
    settings: ['Back to Settings', 'Back to Settings'],
  }
  if (!from || seg[0] === 'outputs' || seg[0] === 'read') return { to: '/outputs', label: 'Outputs' }
  if (!seg.length) return { to: from, label: 'Back to Today' }
  const hit = named[seg[0]]
  return { to: from, label: hit ? hit[seg.length > 1 ? 1 : 0] : 'Back' }
}

/** Minutes to read, at an unhurried 220 words a minute (never less than 1). */
export function readMinutes(markdown: string): number {
  const words = markdown.split(/\s+/).filter((w) => /[\p{L}\p{N}]/u.test(w)).length
  return Math.max(1, Math.round(words / 220))
}

/** "Monday 16 November · 07:02" in the viewer's locale; '' if unparseable. */
export function dateline(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const opts: Intl.DateTimeFormatOptions = { weekday: 'long', day: 'numeric', month: 'long' }
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
  const day = d.toLocaleDateString(undefined, opts)
  const time = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  return `${day} · ${time}`
}
