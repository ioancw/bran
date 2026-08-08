// Delegated copy-button handler for rendered code blocks. Lives apart from
// markdown.ts so App.svelte can install it without statically pulling the
// whole KaTeX/Prism pipeline into the main chunk (that module is lazy now).

/** Install once, at app boot. */
export function installCodeCopyHandler(): void {
  document.addEventListener('click', (e) => {
    const target = e.target as HTMLElement
    const btn = target.closest<HTMLButtonElement>('.md-copy-btn')
    if (!btn) return
    e.preventDefault()
    const code = btn.closest('pre')?.querySelector('code')?.textContent || ''
    navigator.clipboard.writeText(code).then(() => {
      const original = btn.textContent
      btn.textContent = 'copied!'
      btn.classList.add('copied')
      setTimeout(() => {
        btn.textContent = original || 'copy'
        btn.classList.remove('copied')
      }, 1500)
    })
  })
}
