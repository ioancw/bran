// Tiny history-based router. The SPA is served under /app, so routes are
// app-relative (e.g. '/runs' lives at '/app/runs'). One reactive `router` rune
// drives the outlet in App.svelte.

import { tick } from 'svelte'
import { originOf } from './reading'

const BASE = '/app'

export interface Route {
  path: string // app-relative, no query, e.g. '/chat/abc'
  segments: string[]
  query: URLSearchParams
}

function currentFull(): string {
  let p = window.location.pathname + window.location.search
  if (p.startsWith(BASE)) p = p.slice(BASE.length)
  if (!p.startsWith('/')) p = '/' + p
  return p || '/'
}

function toRoute(full: string): Route {
  const [pathPart, queryPart = ''] = full.split('?')
  const path = pathPart.split('#')[0] || '/'
  return {
    path,
    segments: path.split('/').filter(Boolean),
    query: new URLSearchParams(queryPart),
  }
}

/** The page this history entry was reached from, kept on the entry itself so
 * it survives Back, Forward and a reload. Null when it was opened directly. */
function storedFrom(): string | null {
  const from = (window.history.state as { from?: unknown } | null)?.from
  return typeof from === 'string' ? from : null
}

export const router = $state<{ route: Route; full: string; from: string | null }>({
  route: toRoute(currentFull()),
  full: currentFull(),
  from: storedFrom(),
})

/** True when moving between these two routes should cross-fade. Not within
 * the chat (a new conversation's URL pivot happens mid-stream, and freezing
 * the page for a transition there would stall the reply), and not when only
 * the query changes. */
export function shouldTransition(from: Route, to: Route): boolean {
  if (from.path === to.path) return false
  return !(from.segments[0] === 'chat' && to.segments[0] === 'chat')
}

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => void | Promise<void>) => unknown
}

export function navigate(to: string): void {
  if (to === router.full) return
  const next = toRoute(to)
  const apply = () => {
    const from = originOf(router.full, router.from, to)
    window.history.pushState({ from }, '', BASE + to)
    router.route = next
    router.full = to
    router.from = from
    window.scrollTo(0, 0)
  }
  const doc = document as ViewTransitionDocument
  const calm = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
  if (doc.startViewTransition && !calm && shouldTransition(router.route, next)) {
    // The browser snapshots the old page, runs the update, then fades to the
    // new one (see ::view-transition-* in global.css). The update must have
    // reached the DOM when the callback settles, hence the tick.
    doc.startViewTransition(async () => {
      apply()
      await tick()
    })
  } else {
    apply()
  }
}

/** Like navigate(), but rewrites the current history entry instead of adding
 * one — for consuming a one-shot query parameter (e.g. ?prompt=) so that Back
 * or a reload cannot replay it. */
export function replace(to: string): void {
  if (to === router.full) return
  window.history.replaceState(window.history.state ?? {}, '', BASE + to)
  router.route = toRoute(to)
  router.full = to
}

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', () => {
    router.route = toRoute(currentFull())
    router.full = currentFull()
    router.from = storedFrom()
  })
}

/** Internal-link behaviour for `<a href={href('/runs')} use:link>`.
 *
 *  Call sites keep writing a normal `href` so the markup stays declarative and
 *  the element keeps all its `<a>`/`.card`/`.nav-item` styling — but the action
 *  *removes* the attribute from the DOM. With no `href`, the browser has no URL
 *  to show in its bottom-left hover preview (the thing we're suppressing); we
 *  instead drive navigation from a click/Enter handler and expose the element
 *  to assistive tech as a link. Tradeoff (chosen deliberately): no native
 *  open-in-new-tab / middle-click / copy-link — fine for this localhost app.
 *
 *  Svelte re-sets `href` whenever the bound expression changes on a *reused*
 *  list row (unkeyed `{#each}`), so a MutationObserver re-reads the fresh target
 *  and strips the attribute again — keeping navigation correct and the preview
 *  gone. */
export function link(node: HTMLAnchorElement) {
  let target = node.getAttribute('href') || ''
  const strip = () => {
    if (node.hasAttribute('href')) {
      target = node.getAttribute('href') || target
      node.removeAttribute('href')
    }
  }
  strip()
  if (!node.hasAttribute('role')) node.setAttribute('role', 'link')
  if (!node.hasAttribute('tabindex')) node.setAttribute('tabindex', '0')
  node.style.cursor = 'pointer'

  const obs = new MutationObserver(strip)
  obs.observe(node, { attributes: true, attributeFilter: ['href'] })

  const go = () => {
    if (!target.startsWith(BASE)) return
    navigate(target.slice(BASE.length) || '/')
  }
  const onClick = (e: MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    go()
  }
  const onKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      go()
    }
  }
  node.addEventListener('click', onClick)
  node.addEventListener('keydown', onKeydown)
  return {
    destroy() {
      obs.disconnect()
      node.removeEventListener('click', onClick)
      node.removeEventListener('keydown', onKeydown)
    },
  }
}

/** Build a full href (with /app base) for an app-relative path. */
export function href(to: string): string {
  return BASE + to
}
