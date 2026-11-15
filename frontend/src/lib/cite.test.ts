import { describe, it, expect } from 'vitest'
import { citeify } from './cite'
import { renderMarkdown } from './markdown'

const sources = [
  {
    domain: 'bbc.co.uk',
    url: 'https://www.bbc.co.uk/news/business',
    title: 'Business "news"',
    via: 'fetched',
  },
  { domain: 'ft.com', url: null },
  { domain: 'ft.com', url: 'https://www.ft.com/markets', title: null, via: 'cited' },
]

function chips(html: string): HTMLElement[] {
  const box = document.createElement('div')
  box.innerHTML = html
  return Array.from(box.querySelectorAll<HTMLElement>('.cite'))
}

describe('citeify', () => {
  it('turns a citation into a numbered, linked chip', () => {
    const [chip] = chips(citeify('<p>Oil fell (Source: bbc.co.uk).</p>', sources))
    expect(chip.tagName).toBe('A')
    expect(chip.getAttribute('href')).toBe('https://www.bbc.co.uk/news/business')
    expect(chip.getAttribute('rel')).toBe('noopener noreferrer')
    expect(chip.textContent).toBe('1bbc.co.uk')
    expect(chip.dataset.citeN).toBe('1')
    expect(chip.dataset.citeTitle).toBe('Business "news"')
    expect(chip.dataset.citeVia).toBe('fetched')
  })

  it('works on what the markdown renderer actually produces (auto-linked domains)', () => {
    const html = citeify(
      renderMarkdown('Oil fell (Source: reuters.com) and (Sources: bbc.co.uk, ft.com).'),
      sources,
    )
    const found = chips(html)
    expect(found.map((c) => c.dataset.citeDomain)).toEqual(['reuters.com', 'bbc.co.uk', 'ft.com'])
    // The brackets and the renderer's own links are gone; the prose remains.
    expect(html).not.toMatch(/\(Sources?:/)
    expect(html).not.toContain('http://reuters.com')
    expect(html).toContain('Oil fell')
  })

  it('numbers by first appearance and links the entry that has a URL', () => {
    const [chip] = chips(citeify('(Source: www.FT.com)', sources))
    expect(chip.dataset.citeN).toBe('2')
    expect(chip.getAttribute('href')).toBe('https://www.ft.com/markets')
  })

  it('renders an unknown or link-less source as a plain chip', () => {
    const [chip] = chips(citeify('(Source: unknown.example)', sources))
    expect(chip.tagName).toBe('SPAN')
    expect(chip.dataset.citeN).toBeUndefined()
    expect(chip.textContent).toBe('unknown.example')
  })

  it('never links to anything but http(s), and escapes what it inserts', () => {
    const hostile = [
      { domain: 'evil.example', url: 'javascript:alert(1)', title: '"><img src=x onerror=alert(1)>' },
    ]
    const html = citeify('(Source: evil.example)', hostile)
    const [chip] = chips(html)
    expect(chip.tagName).toBe('SPAN')
    expect(html).not.toContain('javascript:')
    expect(html).not.toContain('<img')
    expect(chip.dataset.citeTitle).toBe('"><img src=x onerror=alert(1)>')
  })

  it('leaves other brackets, and other markup, alone', () => {
    const plain = '<p>Prices rose (see chart) and <a href="https://x.example">x</a>.</p>'
    expect(citeify(plain, sources)).toBe(plain)
    const withTag = '<p>(Source: <em>bbc.co.uk</em>)</p>'
    expect(citeify(withTag, sources)).toBe(withTag)
  })
})
