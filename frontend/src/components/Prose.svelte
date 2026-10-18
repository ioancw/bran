<script lang="ts" module>
  // The markdown pipeline (markdown-it + KaTeX + Prism grammars) is the
  // heaviest code in the app, so it loads as its own chunk on first use —
  // one shared promise across every Prose instance.
  let loader: Promise<typeof import('../lib/markdown')> | null = null
  const loadMd = () => (loader ??= import('../lib/markdown'))

  // "(Source: bbc.co.uk)" as the agents write it. Runs over the *rendered*
  // HTML's text, so the pattern can't contain markup; the replacement escapes
  // everything it inserts and only ever links to a URL from the sources list.
  const CITE = /\((?:Source|Sources):\s*([^)<]{1,200})\)/gi
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

  export function citeify(html: string, sources: { domain: string; url: string | null }[]): string {
    if (!sources.length && !/\((?:Source|Sources):/i.test(html)) return html
    const byDomain: Record<string, string | null> = {}
    for (const s of sources) if (!(s.domain in byDomain)) byDomain[s.domain] = s.url
    return html.replace(CITE, (_m, list: string) => {
      const chips = list
        .split(/[,;]\s*|\s+and\s+/)
        .map((d) => d.trim().replace(/^[`*_\s]+|[`*_\s]+$/g, ''))
        .filter(Boolean)
        .map((d) => {
          const key = d.toLowerCase().replace(/^www\./, '')
          const url = byDomain[key]
          const safe = url && /^https?:\/\//i.test(url) ? url : null
          return safe
            ? `<a class="cite" href="${esc(safe)}" target="_blank" rel="noopener noreferrer" title="${esc(safe)}">${esc(key)}</a>`
            : `<span class="cite" title="Cited by domain only">${esc(key)}</span>`
        })
      return chips.join(' ')
    })
  }
</script>

<script lang="ts">
  // Renders markdown + KaTeX. Reactive: re-renders as `text` grows during
  // streaming. The container class `.prose-md` matches global.css styling;
  // pass class="reading" for the serif reading column (outputs, run results).
  // `sources` (optional) turns "(Source: domain)" citations into chips that
  // link to the matching source.
  import type { SourceRef } from '../lib/types'

  let {
    text,
    streaming = false,
    class: klass = '',
    sources = [],
  }: { text: string; streaming?: boolean; class?: string; sources?: SourceRef[] } = $props()

  let renderFn = $state<((src: string) => string) | null>(null)
  void loadMd().then((m) => (renderFn = m.renderMarkdown))

  const html = $derived(renderFn ? citeify(renderFn(text), sources) : null)
</script>

<div class="prose-md {klass}">
  {#if html != null}
    <!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes by construction AND runs DOMPurify over the final document; citeify only inserts escaped text and vetted http(s) URLs -->
    {@html html}{#if streaming}<span class="stream-cursor"></span>{/if}
  {:else}
    <!-- Pipeline chunk still fetching (first render only): show the raw text
         rather than a blank — it swaps to rendered markdown in place. -->
    <div style="white-space: pre-wrap;">{text}</div>
  {/if}
</div>
