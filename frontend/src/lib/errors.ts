// Turn a thrown value into a short, human message. ApiErrors carry the
// server's actual `detail` text — show that first, so the user can self-correct
// ("cron or run_at required") instead of staring at "Request failed (400)".

import { ApiError } from './api'

function byStatus(status: number): string {
  if (status === 404) return 'Not found.'
  if (status === 401 || status === 403) return 'Not authorized.'
  if (status >= 500) return `Server error (${status}). Try again in a moment.`
  return `Request failed (${status}).`
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    return e.detail || byStatus(e.status)
  }
  const raw = e instanceof Error ? e.message : String(e)
  if (/failed to fetch|networkerror|load failed|err_connection/i.test(raw)) {
    return "Can't reach the server — is `bran serve` running?"
  }
  // Pages often stringify errors before they get here ("ApiError: <detail>") —
  // strip the prefix so the server's message reads clean.
  const cleaned = raw.replace(/^(Api)?Error:\s*/, '')
  const code = cleaned.match(/^\b(\d{3})\b/)?.[1]
  if (code) return byStatus(Number(code))
  return cleaned
}
