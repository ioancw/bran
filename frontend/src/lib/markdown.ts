// Markdown + KaTeX rendering, ported from base.html's inline pipeline.
//
// Delimiters: \(...\) inline, \[...\] and $$...$$ display. Single $...$ is
// deliberately NOT math (so finance prices like "$0.46" survive). Math is
// pulled out before markdown runs (markdown-it would mangle \( \)), then the
// placeholders are replaced with KaTeX HTML.

import MarkdownIt from 'markdown-it'
import katex from 'katex'
import Prism from 'prismjs'
// Language grammars (dependency order: each registers on import). javascript +
// markup/css live in Prism core; the rest are a focused set for bran's work.
import 'prismjs/components/prism-typescript'
import 'prismjs/components/prism-python'
import 'prismjs/components/prism-bash'
import 'prismjs/components/prism-json'
import 'prismjs/components/prism-sql'
import 'prismjs/components/prism-yaml'
import 'prismjs/components/prism-toml'
import 'prismjs/components/prism-go'
import 'prismjs/components/prism-rust'
import 'prismjs/components/prism-r'
import 'prismjs/components/prism-markdown'
import 'prismjs/components/prism-diff'

let _md: MarkdownIt | null = null

function escapeAttr(s: string): string {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// Common language aliases → Prism grammar keys.
const LANG_ALIAS: Record<string, string> = {
  js: 'javascript', jsx: 'javascript', node: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', sh: 'bash', shell: 'bash', zsh: 'bash',
  yml: 'yaml', md: 'markdown', rs: 'rust', golang: 'go',
}

/** Syntax-highlight `code` to token HTML, falling back to escaped plain text
 *  when the language is unknown or Prism throws. Result is HTML-safe. */
function highlightCode(code: string, lang: string, escape: (s: string) => string): string {
  const key = LANG_ALIAS[lang.toLowerCase()] || lang.toLowerCase()
  const grammar = key && Prism.languages[key]
  if (grammar) {
    try {
      return Prism.highlight(code, grammar, key)
    } catch {
      /* fall through to plain */
    }
  }
  return escape(code)
}

function makeMd(): MarkdownIt {
  const md = new MarkdownIt({ html: false, linkify: true, breaks: false })
  const renderBlock = (code: string, lang: string) => {
    const langLabel = lang || 'code'
    const langClass = lang ? ` class="language-${escapeAttr(lang)}"` : ''
    const codeHtml = highlightCode(code, lang, md.utils.escapeHtml)
    return (
      '<pre class="md-code"><div class="md-code-header">' +
      '<span class="md-code-lang">' +
      escapeAttr(langLabel) +
      '</span>' +
      '<button type="button" class="md-copy-btn" data-code="' +
      escapeAttr(code) +
      '">copy</button>' +
      '</div><code' +
      langClass +
      '>' +
      codeHtml +
      '</code></pre>'
    )
  }
  md.renderer.rules.fence = (tokens, idx) => {
    const t = tokens[idx]
    return renderBlock(t.content, t.info.trim().split(/\s+/)[0] || '')
  }
  md.renderer.rules.code_block = (tokens, idx) => renderBlock(tokens[idx].content, '')
  return md
}

export function renderMarkdown(src: string): string {
  if (!_md) _md = makeMd()
  const md = _md

  let text = src
  const mathBlocks: { content: string; displayMode: boolean }[] = []
  const placeholder = (i: number) => `¤¤BRAN_MATH_${i}¤¤`
  const extract = (re: RegExp, displayMode: boolean) => {
    text = text.replace(re, (_m, content: string) => {
      mathBlocks.push({ content, displayMode })
      return placeholder(mathBlocks.length - 1)
    })
  }
  extract(/\\\[([\s\S]+?)\\\]/g, true) // \[ ... \]
  extract(/\$\$([\s\S]+?)\$\$/g, true) // $$ ... $$
  extract(/\\\(([\s\S]+?)\\\)/g, false) // \( ... \)

  const rendered = md.render(text)

  return rendered.replace(/¤¤BRAN_MATH_(\d+)¤¤/g, (_m, idx: string) => {
    const block = mathBlocks[+idx]
    if (!block) return ''
    try {
      return katex.renderToString(block.content, {
        displayMode: block.displayMode,
        throwOnError: false,
        errorColor: 'var(--red)',
      })
    } catch {
      return block.content
    }
  })
}

/** Delegated copy-button handler for rendered code blocks. Install once. */
export function installCodeCopyHandler(): void {
  document.addEventListener('click', (e) => {
    const target = e.target as HTMLElement
    const btn = target.closest<HTMLButtonElement>('.md-copy-btn[data-code]')
    if (!btn) return
    e.preventDefault()
    const code = btn.dataset.code || ''
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
