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
