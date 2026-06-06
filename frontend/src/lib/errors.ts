// Turn a thrown value (often an Error from the api client, like
// "500 Internal Server Error for /spa/...") into a short, human message.

export function errorText(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  if (/failed to fetch|networkerror|load failed|err_connection/i.test(raw)) {
    return "Can't reach the server — is `bran serve` running?"
  }
  const code = raw.match(/\b(\d{3})\b/)?.[1]
  if (code) {
    if (code === '404') return 'Not found.'
    if (code === '401' || code === '403') return 'Not authorized.'
    if (code.startsWith('5')) return `Server error (${code}). Try again in a moment.`
    return `Request failed (${code}).`
  }
  return raw.replace(/^Error:\s*/, '')
}
