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

/** Click handler for internal links: <a href={href('/runs')} use:link>. */
export function link(node: HTMLAnchorElement) {
  const onClick = (e: MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    const hrefAttr = node.getAttribute('href') || ''
    if (!hrefAttr.startsWith(BASE)) return
    e.preventDefault()
    navigate(hrefAttr.slice(BASE.length) || '/')
  }
  node.addEventListener('click', onClick)
  return { destroy: () => node.removeEventListener('click', onClick) }
}

/** Build a full href (with /app base) for an app-relative path. */
export function href(to: string): string {
  return BASE + to
}
