// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { compareCells, installTableSort, numericValue } from './sort-tables'

describe('numericValue', () => {
  it('reads the numbers a briefing table holds', () => {
    expect(numericValue('1,234.5')).toBe(1234.5)
    expect(numericValue('$0.46')).toBe(0.46)
    expect(numericValue('-2.1%')).toBe(-2.1)
    expect(numericValue('+0.3%')).toBe(0.3)
    expect(numericValue('(1.5)')).toBe(-1.5)
    expect(numericValue('£3bn')).toBe(3e9)
    expect(numericValue('12k')).toBe(12_000)
    expect(numericValue('25bp')).toBe(25)
  })
  it('is NaN for words', () => {
    expect(numericValue('Brent')).toBeNaN()
    expect(numericValue('Q3 2026')).toBeNaN()
    expect(numericValue('')).toBeNaN()
  })
})

describe('compareCells', () => {
  it('orders numbers as numbers and text as text', () => {
    expect(compareCells('9', '10')).toBeLessThan(0)
    expect(compareCells('-2.1%', '0.4%')).toBeLessThan(0)
    expect(compareCells('apple', 'Banana')).toBeLessThan(0)
    expect(compareCells('item 9', 'item 10')).toBeLessThan(0)
  })
  it('puts empty cells last', () => {
    expect(compareCells('', '5')).toBeGreaterThan(0)
    expect(compareCells('5', ' ')).toBeLessThan(0)
    expect(compareCells('', '')).toBe(0)
  })
})

describe('clicking a heading', () => {
  it('sorts by that column, then reverses, keeping blanks at the bottom', () => {
    document.body.innerHTML = `
      <div class="md-table-wrap"><table>
        <thead><tr><th>Name</th><th>Move</th></tr></thead>
        <tbody>
          <tr><td>Gold</td><td>0.4%</td></tr>
          <tr><td>Brent</td><td>-2.1%</td></tr>
          <tr><td>Gilts</td><td></td></tr>
          <tr><td>FTSE</td><td>1.2%</td></tr>
        </tbody>
      </table></div>`
    installTableSort()
    const names = () =>
      Array.from(document.querySelectorAll('tbody tr')).map((r) => r.children[0].textContent)
    const move = document.querySelectorAll('th')[1]
    move.click()
    expect(names()).toEqual(['Brent', 'Gold', 'FTSE', 'Gilts'])
    expect(move.getAttribute('aria-sort')).toBe('ascending')
    move.click()
    expect(names()).toEqual(['FTSE', 'Gold', 'Brent', 'Gilts'])
    expect(move.getAttribute('aria-sort')).toBe('descending')
    document.querySelectorAll('th')[0].click()
    expect(names()).toEqual(['Brent', 'FTSE', 'Gilts', 'Gold'])
    expect(move.hasAttribute('aria-sort')).toBe(false)
  })
})
