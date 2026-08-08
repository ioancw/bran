// The markdown pipeline renders adversarial content (LLM output quoting
// fetched web pages) into {@html}, so these tests are safety tests first and
// rendering tests second.
import { describe, it, expect } from 'vitest'
import { renderMarkdown } from './markdown'

describe('renderMarkdown — rendering', () => {
  it('renders basic markdown', () => {
    const html = renderMarkdown('# Title\n\nSome **bold** text.')
    expect(html).toContain('<h1>Title</h1>')
    expect(html).toContain('<strong>bold</strong>')
  })

  it('renders display and inline math via KaTeX', () => {
    const html = renderMarkdown('Euler: \\(e^{i\\pi} = -1\\)\n\n$$\\frac{a}{b}$$')
    expect(html).toContain('katex')
    // Both delimiters extracted — no raw \( or $$ left behind.
    expect(html).not.toContain('\\(')
    expect(html).not.toContain('$$')
  })

  it('leaves single-dollar prices alone (not math)', () => {
    const html = renderMarkdown('It costs $0.46 today and $1.20 tomorrow.')
    expect(html).toContain('$0.46')
    expect(html).not.toContain('katex')
  })

  it('keeps math delimiters inside code literal', () => {
    const html = renderMarkdown('Use `\\(x\\)` in LaTeX:\n\n```tex\n\\(a+b\\)\n```')
    expect(html).not.toContain('katex')
    expect(html).toContain('md-code')
  })

  it('highlights fenced code and shows the language label + copy button', () => {
    const html = renderMarkdown('```python\nprint("hi")\n```')
    expect(html).toContain('language-python')
    expect(html).toContain('md-copy-btn')
    expect(html).toContain('md-code-lang')
  })

  it('tolerates an unclosed fence mid-stream', () => {
    const html = renderMarkdown('```python\nprint("streaming...')
    expect(html).toContain('md-code')
  })

  it('wraps tables for horizontal scroll', () => {
    const html = renderMarkdown('| a | b |\n|---|---|\n| 1 | 2 |')
    expect(html).toContain('md-table-wrap')
  })
})

describe('renderMarkdown — safety', () => {
  it('escapes raw HTML in the source', () => {
    // The hostile text may survive as escaped TEXT ("&lt;img …"), which is
    // fine — what must never appear is a live tag.
    const html = renderMarkdown('hello <script>alert(1)</script> <img src=x onerror=alert(1)>')
    expect(html).not.toMatch(/<script|<img/i)
  })

  it('escapes HTML smuggled through math content', () => {
    const html = renderMarkdown('\\(x <img src=x onerror=alert(1)>\\)')
    expect(html).not.toMatch(/<script|<img/i)
  })

  it('does not treat placeholder-shaped source text as a substitution slot', () => {
    // A literal placeholder token in the source, alongside REAL math so the
    // substitution pass actually runs. Without the per-render nonce this
    // spliced KaTeX markup into an attacker-chosen position.
    const html = renderMarkdown('start ¤¤BRAN_MATH_0¤¤ end\n\nreal: \\(a+b\\)')
    expect(html).toContain('¤¤BRAN_MATH_0¤¤')
  })

  it('does not resurrect masked code via placeholder-shaped text', () => {
    const html = renderMarkdown('fake ¤¤BRAN_CODE_0¤¤ here\n\n`real code`')
    expect(html).toContain('¤¤BRAN_CODE_0¤¤')
    expect(html).toContain('<code>real code</code>')
  })

  it('sanitizes javascript: link destinations', () => {
    const html = renderMarkdown('[click](javascript:alert(1))')
    expect(html.toLowerCase()).not.toContain('href="javascript:')
  })

  it('escapes hostile fence language labels', () => {
    const html = renderMarkdown('```"><img src=x onerror=alert(1)>\ncode\n```')
    expect(html).not.toContain('onerror=')
  })
})
