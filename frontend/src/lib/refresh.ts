// "Stay fresh when the user comes back": runners fire and runs finish while
// the tab sits in the background, so pages reload their data when the window
// regains focus or the tab becomes visible again.

/** Call `fn` whenever the user returns to this tab. Returns the cleanup, so it
 * drops straight into an effect: `$effect(() => onReturn(() => void load()))`. */
export function onReturn(fn: () => void): () => void {
  const refresh = () => {
    if (document.visibilityState === 'visible') fn()
  }
  window.addEventListener('focus', refresh)
  document.addEventListener('visibilitychange', refresh)
  return () => {
    window.removeEventListener('focus', refresh)
    document.removeEventListener('visibilitychange', refresh)
  }
}
