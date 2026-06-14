// Auth-failure state. The /spa surface is same-origin/localhost (no token), so
// a 401/403 here means something structural — a reverse proxy demanding auth, a
// cross-origin misconfiguration — and every request will keep failing the same
// way. Rather than letting each page show its own dead-end error, the api
// client flags it here once and App.svelte renders a single recovery banner.

export const authState = $state<{ blocked: boolean; message: string }>({
  blocked: false,
  message: '',
})

export function flagAuthError(message: string): void {
  authState.blocked = true
  authState.message = message || 'The server rejected this session.'
}

export function clearAuthError(): void {
  authState.blocked = false
  authState.message = ''
}
