// Tiny history-based router. The SPA is served under /app, so routes are
// app-relative (e.g. '/runs' lives at '/app/runs'). One reactive `router` rune
// drives the outlet in App.svelte.

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

export const router = $state<{ route: Route; full: string }>({
  route: toRoute(currentFull()),
  full: currentFull(),
})

export function navigate(to: string): void {
  if (to === router.full) return
  window.history.pushState({}, '', BASE + to)
  router.route = toRoute(to)
  router.full = to
  window.scrollTo(0, 0)
}

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', () => {
    router.route = toRoute(currentFull())
    router.full = currentFull()
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
    if (e.key === 'Enter') { e.preventDefault(); go() }
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
