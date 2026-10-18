// Product vocabulary for values the backend stores as identifiers. The DB says
// `spawn`; a person reads "Background". Use these everywhere a source or
// status is shown as text, so the app speaks one language.

const SOURCE_LABELS: Record<string, string> = {
  chat: 'Chat',
  runner: 'Runner',
  spawn: 'Background',
  subagent: 'Subagent',
  manual: 'Manual',
  synthesis: 'Synthesis',
}

/** "Background" for `spawn`, etc. Unknown values are capitalised as-is. */
export function sourceLabel(source: string | null | undefined): string {
  if (!source) return ''
  return SOURCE_LABELS[source] ?? source.charAt(0).toUpperCase() + source.slice(1)
}

/** One-line description of a source, for tooltips. */
export function sourceHint(source: string | null | undefined): string {
  switch (source) {
    case 'runner':
      return 'Fired by a scheduled runner'
    case 'spawn':
      return 'A background run started from a conversation'
    case 'subagent':
      return 'An inline subagent a run delegated to'
    case 'synthesis':
      return 'The combined answer bran wrote when a fan-out finished'
    case 'manual':
      return 'Started by hand (Run now, CLI or API)'
    case 'chat':
      return 'A conversation turn'
    default:
      return ''
  }
}
