// Click a column heading of a rendered markdown table to sort by it (again to
// reverse). Delegated at the document, like the code-copy handler, because the
// tables are plain HTML inside rendered prose.

/** "1,234.5", "$0.46", "-2.1%", "£3bn" → a number; anything else → NaN. */
export function numericValue(text: string): number {
  const t = text
    .trim()
    .replace(/[\s,]/g, '')
    .replace(/^[+]/, '')
    .replace(/^\(([^)]+)\)$/, '-$1')
  const m = t.match(/^(-?)[$£€¥]?(-?\d+(?:\.\d+)?)(%|[kmb]n?|bp|bps|x)?$/i)
  if (!m) return NaN
  const n = parseFloat(m[2]) * (m[1] ? -1 : 1)
  const unit = (m[3] ?? '').toLowerCase()
  const scale = unit.startsWith('k')
    ? 1e3
    : unit.startsWith('m')
      ? 1e6
      : unit.startsWith('b') && unit !== 'bp' && unit !== 'bps'
        ? 1e9
        : 1
  return n * scale
}

/** Compare two cell texts: as numbers when both are, else as text. Empty
 * cells sort last either way round. */
export function compareCells(a: string, b: string): number {
  const ea = !a.trim()
  const eb = !b.trim()
  if (ea || eb) return ea === eb ? 0 : ea ? 1 : -1
  const na = numericValue(a)
  const nb = numericValue(b)
  if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb
  return a.trim().localeCompare(b.trim(), undefined, { numeric: true, sensitivity: 'base' })
}

function sortBy(th: HTMLTableCellElement): void {
  const table = th.closest('table')
  const body = table?.tBodies[0]
  const headRow = th.parentElement as HTMLTableRowElement | null
  if (!table || !body || !headRow) return
  const col = Array.from(headRow.cells).indexOf(th)
  const descending = th.getAttribute('aria-sort') === 'ascending'
  for (const cell of Array.from(headRow.cells)) cell.removeAttribute('aria-sort')
  th.setAttribute('aria-sort', descending ? 'descending' : 'ascending')
  const rows = Array.from(body.rows)
  const text = (r: HTMLTableRowElement) => r.cells[col]?.textContent ?? ''
  const blank = (r: HTMLTableRowElement) => !text(r).trim()
  rows.sort((x, y) => {
    // Empty cells stay at the bottom in both directions.
    if (blank(x) || blank(y)) return compareCells(text(x), text(y))
    const c = compareCells(text(x), text(y))
    return descending ? -c : c
  })
  for (const r of rows) body.appendChild(r)
}

/** Install once, at app boot. */
export function installTableSort(): void {
  document.addEventListener('click', (e) => {
    const target = e.target as HTMLElement
    const th = target.closest<HTMLTableCellElement>('.md-table-wrap thead th')
    if (!th) return
    // Selecting text in a heading, or following a link in it, is not a sort.
    if (target.closest('a') || window.getSelection()?.toString()) return
    sortBy(th)
  })
}
