// Markdown + KaTeX rendering, ported from base.html's inline pipeline.
//
// Delimiters: \(...\) inline, \[...\] and $$...$$ display. Single $...$ is
// deliberately NOT math (so finance prices like "$0.46" survive). Math is
// pulled out before markdown runs (markdown-it would mangle \( \)), then the
// placeholders are replaced with KaTeX HTML.

// This module is loaded LAZILY (dynamic import in Prose.svelte) — KaTeX,
// Prism, and the grammars below are the heaviest things in the app, and the
// dashboard shouldn't pay for them before first paint. Don't import it
// statically from anything on the boot path.
import 'katex/dist/katex.min.css' // rides in this lazy chunk, not the boot CSS
import MarkdownIt from 'markdown-it'
import katex from 'katex'

// Symbols LaTeX users write that KaTeX has no command for: the wasysym /
// marvosym astronomy set. Rendered as the Unicode glyph; without this, KaTeX
// prints the bare command name in red. Mirrored in bran/doc_export.py.
export const KATEX_MACROS: Record<string, string> = {
  '\\Moon': '\\text{☾}',
  '\\leftmoon': '\\text{☾}',
  '\\rightmoon': '\\text{☽}',
  '\\Sun': '\\odot',
  '\\Earth': '\\oplus',
  '\\Mercury': '\\text{☿}',
  '\\Venus': '\\text{♀}',
  '\\Mars': '\\text{♂}',
  '\\Jupiter': '\\text{♃}',
  '\\Saturn': '\\text{♄}',
  '\\astrosun': '\\odot',
  '\\degree': '^{\\circ}',
  '\\celsius': '^{\\circ}\\mathrm{C}',
}
import DOMPurify from 'dompurify'
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
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

// Common language aliases → Prism grammar keys.
const LANG_ALIAS: Record<string, string> = {
  js: 'javascript',
  jsx: 'javascript',
  node: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  md: 'markdown',
  rs: 'rust',
  golang: 'go',
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
      // The copy handler reads the code back out of the <code> element's
      // textContent — carrying it in an attribute meant later placeholder
      // substitution could inject markup into the attribute and break out.
      '<button type="button" class="md-copy-btn">copy</button>' +
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
  // Wide tables scroll inside their own wrapper instead of crushing the prose
  // column (see .md-table-wrap in global.css).
  md.renderer.rules.table_open = () => '<div class="md-table-wrap"><table>\n'
  md.renderer.rules.table_close = () => '</table></div>\n'
  return md
}

export function renderMarkdown(src: string): string {
  if (!_md) _md = makeMd()
  const md = _md

  let text = src

  // Placeholders carry a per-render nonce: the source is LLM/web-influenced,
  // and a literal "¤¤BRAN_MATH_0¤¤" in it must never match a substitution
  // below — that would splice KaTeX markup into arbitrary (already-escaped)
  // positions of the rendered document.
  const nonce = Math.random().toString(36).slice(2, 10)

  // Math delimiters inside code must stay literal — mask fences and inline
  // code spans before math extraction, restore them before markdown runs.
  const codeBlocks: string[] = []
  const maskCode = (re: RegExp) => {
    text = text.replace(re, (m) => {
      codeBlocks.push(m)
      return `¤¤BRAN_CODE_${nonce}_${codeBlocks.length - 1}¤¤`
    })
  }
  // Fenced blocks (```/~~~), tolerating an unclosed fence mid-stream.
  maskCode(/^([`~]{3,}).*\n[\s\S]*?(?:^\1[`~]*[ \t]*$|(?![\s\S]))/gm)
  // Inline code spans (`...`, ``...``).
  maskCode(/(`+)(?!`)[\s\S]*?\1/g)

  const mathBlocks: { content: string; displayMode: boolean }[] = []
  const placeholder = (i: number) => `¤¤BRAN_MATH_${nonce}_${i}¤¤`
  const extract = (re: RegExp, displayMode: boolean) => {
    text = text.replace(re, (_m, content: string) => {
      mathBlocks.push({ content, displayMode })
      return placeholder(mathBlocks.length - 1)
    })
  }
  extract(/\\\[([\s\S]+?)\\\]/g, true) // \[ ... \]
  extract(/\$\$([\s\S]+?)\$\$/g, true) // $$ ... $$
  extract(/\\\(([\s\S]+?)\\\)/g, false) // \( ... \)

  text = text.replace(
    new RegExp(`¤¤BRAN_CODE_${nonce}_(\\d+)¤¤`, 'g'),
    (_m, idx: string) => codeBlocks[+idx] ?? '',
  )

  const rendered = md.render(text)

  const withMath = rendered.replace(new RegExp(`¤¤BRAN_MATH_${nonce}_(\\d+)¤¤`, 'g'), (_m, idx: string) => {
    const block = mathBlocks[+idx]
    if (!block) return ''
    try {
      return katex.renderToString(block.content, {
        displayMode: block.displayMode,
        throwOnError: false,
        errorColor: 'var(--red)',
        macros: KATEX_MACROS,
      })
    } catch {
      // KaTeX can still throw with throwOnError:false (internal errors); the
      // source is LLM/web-influenced, so it must not reach {@html} unescaped.
      return md.utils.escapeHtml(block.content)
    }
  })

  // Final backstop before {@html}: everything above is escaped-by-construction,
  // but this pipeline renders adversarial content (LLM output quoting fetched
  // web pages) — sanitize the assembled document rather than trusting the
  // assembly. Default profile keeps KaTeX's MathML/spans and Prism's markup.
  return DOMPurify.sanitize(withMath)
}
