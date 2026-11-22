// "(Source: bbc.co.uk)" — how bran's agents cite — turned into chips in the
// rendered answer. Runs over HTML that renderMarkdown has already escaped and
// sanitised; everything inserted here is escaped again, and a chip only ever
// links to a URL taken from the sources list.

// The markdown renderer auto-links a bare domain, so by the time we see it a
// citation reads `(Source: <a href="http://bbc.co.uk">bbc.co.uk</a>)`. Allow
// those anchors inside the brackets, and nothing else that is markup.
const CITE = /\((?:Source|Sources):\s*((?:[^)<]|<a\b[^>]*>[^<]*<\/a>){1,600})\)/gi
const TAGS = /<[^>]+>/g

const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** The site a bare web address should be shown as, or null to leave it as
 * written. "https://www.ft.com/content/abc" reads as "ft.com"; a bare
 * site ("ft.com", which is already short) and anything that is not a plain
 * http(s) address are left alone. `text` is what the report wrote. */
export function linkChip(href: string, text: string): string | null {
  if (!/^(https?:\/\/|www\.)/i.test(text.trim())) return null
  let url: URL
  try {
    url = new URL(href)
  } catch {
    return null
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
  if (url.pathname.length <= 1 && !url.search) return null
  const host = url.hostname.toLowerCase().replace(/^www\./, '')
  return /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(host) ? host : null
}

export interface CiteSource {
  domain: string
  url: string | null
  title?: string | null
  via?: string
}

/** Turn each "(Source: …)" into chips: the source's number in `sources` (the
 * order the Sources row shows them) and its domain. What the hover card shows
 * rides on the chip as data attributes (see CitePopover). */
export function citeify(html: string, sources: CiteSource[]): string {
  if (!/\((?:Source|Sources):/i.test(html)) return html
  const byDomain: Record<string, { s: CiteSource; n: number }> = {}
  sources.forEach((s, i) => {
    const cur = byDomain[s.domain]
    // The first entry for a domain numbers the chip; one with a URL links it.
    if (!cur) byDomain[s.domain] = { s, n: i + 1 }
    else if (!cur.s.url && s.url) byDomain[s.domain] = { s, n: cur.n }
  })
  return html.replace(CITE, (whole, list: string) => {
    const names = list
      .replace(TAGS, '')
      .split(/[,;]\s*|\s+and\s+/)
      .map((d) => d.trim().replace(/^[`*_\s]+|[`*_\s]+$/g, ''))
      .filter(Boolean)
    // Prose in brackets that merely starts with "Source:" is left as written.
    if (!names.length || names.some((d) => d.length > 80)) return whole
    return names
      .map((d) => {
        const key = d.toLowerCase().replace(/^www\./, '')
        const hit = byDomain[key]
        const url = hit?.s.url
        const safe = url && /^https?:\/\//i.test(url) ? url : null
        const data =
          ` data-cite-domain="${esc(key)}"` +
          (hit ? ` data-cite-n="${hit.n}"` : '') +
          (hit?.s.title ? ` data-cite-title="${esc(hit.s.title)}"` : '') +
          (hit?.s.via ? ` data-cite-via="${esc(hit.s.via)}"` : '') +
          (safe ? ` data-cite-url="${esc(safe)}"` : '')
        const body = (hit ? `<span class="cite-n">${hit.n}</span>` : '') + esc(key)
        return safe
          ? `<a class="cite" href="${esc(safe)}" target="_blank" rel="noopener noreferrer"${data}>${body}</a>`
          : `<span class="cite" tabindex="0"${data}>${body}</span>`
      })
      .join(' ')
  })
}
