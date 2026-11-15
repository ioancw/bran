// Where one turn's answer came from, worked out from the turn itself: the
// pages its tools fetched, the links in its text and the domains it cited —
// in the order they first appear, so the list (and the numbers on the
// citation chips) only ever grows while a reply streams in.
//
// The server's per-conversation list (`known`, bran.sources) is used for what
// the turn cannot tell us: page titles, and the URL behind a domain-only
// "(Source: bbc.co.uk)" citation.

import type { SourceRef } from '../lib/types'
import type { ChatItem } from './events'

// Mirrors FETCH_TOOLS in src/bran/sources.py.
export const FETCH_TOOLS = new Set(['WebFetch', 'mcp__bran_docs__fetch_url', 'mcp__tavily__tavily_extract'])

const MD_LINK = /\[([^\]]{1,200})\]\((https?:\/\/[^)\s]+)\)/g
const BARE_URL = /(?<![([])\bhttps?:\/\/[^\s<>")\]]+/g
const CITE = /\((?:Source|Sources):\s*([^)]{1,200})\)/gi
const TRAIL = /[.,;:!?'"]+$/

export function domainOf(url: string): string {
  try {
    const host = new URL(url).hostname.toLowerCase()
    return host.startsWith('www.') ? host.slice(4) : host
  } catch {
    return ''
  }
}

/** One key per page: scheme + host + path (no trailing slash) + query. */
export function normUrl(url: string): string {
  const clean = url.trim().replace(TRAIL, '')
  try {
    const u = new URL(clean)
    const path = u.pathname.replace(/\/+$/, '')
    return `${u.protocol}//${u.hostname.toLowerCase()}${path}${u.search}`
  } catch {
    return clean
  }
}

/** "bbc.co.uk, ft.com and reuters.com" → the domains named in a citation. */
export function citedDomains(list: string): string[] {
  return list
    .split(/[,;]\s*|\s+and\s+/)
    .map((d) =>
      d
        .trim()
        .replace(/^[`*_\s]+|[`*_\s]+$/g, '')
        .toLowerCase()
        .replace(/^www\./, ''),
    )
    .filter((d) => d && d.includes('.') && !d.includes(' '))
}

function urlsFromInput(input: Record<string, unknown>): string[] {
  const raw = input?.url ?? input?.urls
  const parts =
    typeof raw === 'string' ? raw.split(/[,\s]+/) : Array.isArray(raw) ? raw.map((u) => String(u)) : []
  return parts.map((u) => u.trim()).filter((u) => /^https?:\/\//i.test(u))
}

/** Sources of items[start, end), in order of first appearance. */
export function sourcesInRange(
  items: ChatItem[],
  start: number,
  end: number,
  known: SourceRef[] = [],
): SourceRef[] {
  const knownByUrl = new Map<string, SourceRef>()
  const knownByDomain = new Map<string, SourceRef>()
  for (const s of known) {
    if (s.url) knownByUrl.set(normUrl(s.url), s)
    // Prefer an entry that has a URL as the domain's representative.
    const cur = knownByDomain.get(s.domain)
    if (!cur || (!cur.url && s.url)) knownByDomain.set(s.domain, s)
  }

  const out: SourceRef[] = []
  const byKey = new Map<string, SourceRef>()
  const domains = new Set<string>()

  const add = (url: string, title: string | null, via: SourceRef['via']) => {
    const key = normUrl(url)
    const domain = domainOf(key)
    if (!domain) return
    const seen = byKey.get(key)
    if (seen) {
      if (title && !seen.title) seen.title = title
      if (via === 'cited') seen.via = 'cited'
      return
    }
    const src: SourceRef = {
      url: url.trim().replace(TRAIL, ''),
      domain,
      title: title ?? knownByUrl.get(key)?.title ?? null,
      via,
    }
    byKey.set(key, src)
    domains.add(domain)
    out.push(src)
  }

  for (let i = start; i < end && i < items.length; i++) {
    const it = items[i]
    if (it.kind === 'tool' && FETCH_TOOLS.has(it.name)) {
      for (const u of urlsFromInput(it.input)) add(u, null, 'fetched')
    } else if (it.kind === 'assistant') {
      for (const m of it.text.matchAll(MD_LINK)) add(m[2], m[1].trim(), 'cited')
      for (const m of it.text.matchAll(BARE_URL)) add(m[0], null, 'cited')
      for (const m of it.text.matchAll(CITE)) {
        for (const dom of citedDomains(m[1])) {
          const mine = out.find((s) => s.domain === dom)
          if (mine) {
            mine.via = 'cited'
            continue
          }
          const k = knownByDomain.get(dom)
          if (k?.url) add(k.url, k.title, 'cited')
          else if (!domains.has(dom)) {
            domains.add(dom)
            out.push({ url: null, domain: dom, title: null, via: 'cited' })
          }
        }
      }
    }
  }
  return out
}

/** A turn runs from a user message up to the next one. Items before the
 * first user message (a run's transcript has none) form a turn of their own. */
export function turnRanges(items: ChatItem[]): { start: number; end: number }[] {
  const starts: number[] = []
  items.forEach((it, i) => {
    if (it.kind === 'user') starts.push(i)
  })
  if (!starts.length || starts[0] !== 0) starts.unshift(0)
  if (!items.length) return []
  return starts.map((s, k) => ({ start: s, end: k + 1 < starts.length ? starts[k + 1] : items.length }))
}

/** A stable hue (0–359) for a domain's lettered tile. */
export function domainHue(domain: string): number {
  let h = 0
  for (let i = 0; i < domain.length; i++) h = (h * 31 + domain.charCodeAt(i)) >>> 0
  return h % 360
}

/** The path of a URL, for when a source has no title ("/business/live"). */
export function pathLabel(url: string | null): string {
  if (!url) return ''
  try {
    const p = decodeURIComponent(new URL(url).pathname).replace(/\/+$/, '')
    return p.length > 1 ? p.split('/').filter(Boolean).slice(-2).join(' / ') : ''
  } catch {
    return ''
  }
}
