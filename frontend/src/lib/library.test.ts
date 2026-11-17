import { describe, it, expect } from 'vitest'
import { displayTitle, filterLibrary, groupCounts, groupOf, libraryProjects } from './library'
import type { LibraryItem } from './types'

function item(name: string, over: Partial<LibraryItem> = {}): LibraryItem {
  return {
    name,
    title: '',
    size: 10,
    size_human: '10 B',
    modified: 0,
    project_id: 'p1',
    project_name: 'FX vol',
    url: '/spa/projects/p1/files/' + name,
    ...over,
  }
}

const ITEMS = [
  item('walkthrough.html', { title: 'FX vol surface walkthrough' }),
  item('briefing_2026-10-18.md'),
  item('paper.pdf', { project_id: 'p2', project_name: '11 Plus' }),
  item('data.csv', { project_id: null, project_name: null }),
  item('chart.png'),
  item('archive.zip'),
]

describe('library', () => {
  it('groups files by how they read', () => {
    expect(ITEMS.map((i) => groupOf(i.name))).toEqual(['page', 'note', 'pdf', 'note', 'image', 'other'])
  })
  it('titles an item by its document title, else a readable file name', () => {
    expect(displayTitle(ITEMS[0])).toBe('FX vol surface walkthrough')
    expect(displayTitle(ITEMS[1])).toBe('briefing 2026 10 18')
    expect(displayTitle(item('.env'))).toBe('.env')
  })
  it('filters by kind, project and every search word', () => {
    const names = (f: Parameters<typeof filterLibrary>[1]) => filterLibrary(ITEMS, f).map((i) => i.name)
    expect(names({ q: '', group: 'note', project: '' })).toEqual(['briefing_2026-10-18.md', 'data.csv'])
    expect(names({ q: '', group: 'all', project: 'none' })).toEqual(['data.csv'])
    expect(names({ q: '', group: 'all', project: 'p2' })).toEqual(['paper.pdf'])
    expect(names({ q: 'surface FX', group: 'all', project: '' })).toEqual(['walkthrough.html'])
    expect(names({ q: '11 plus', group: 'all', project: '' })).toEqual(['paper.pdf'])
    expect(names({ q: 'surface', group: 'pdf', project: '' })).toEqual([])
  })
  it('counts kinds under the search and project filter, not the chosen kind', () => {
    expect(groupCounts(ITEMS, { q: '', project: '' })).toEqual({
      all: 6,
      page: 1,
      pdf: 1,
      note: 2,
      image: 1,
      other: 1,
    })
    expect(groupCounts(ITEMS, { q: '', project: 'p1' }).all).toBe(4)
  })
  it('lists the projects that hold something and whether anything is loose', () => {
    expect(libraryProjects(ITEMS)).toEqual({
      projects: [
        { id: 'p1', name: 'FX vol' },
        { id: 'p2', name: '11 Plus' },
      ],
      loose: true,
    })
    expect(libraryProjects([ITEMS[0]]).loose).toBe(false)
  })
})
