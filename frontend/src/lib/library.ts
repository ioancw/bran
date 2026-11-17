// The Artifacts page: every document bran holds, across projects. This is the
// part that decides what is shown — grouping by kind, search, project filter.

import { docKind } from './doc-kind'
import type { LibraryItem } from './types'

export type LibraryGroup = 'page' | 'pdf' | 'note' | 'image' | 'other'
export const GROUPS: LibraryGroup[] = ['page', 'pdf', 'note', 'image', 'other']

const GROUP_LABEL: Record<LibraryGroup, string> = {
  page: 'Documents',
  pdf: 'PDFs',
  note: 'Notes',
  image: 'Images',
  other: 'Other',
}
export const groupLabel = (g: LibraryGroup): string => GROUP_LABEL[g]

/** Saved documents are pages; markdown and plain text are both "notes". */
export function groupOf(name: string): LibraryGroup {
  const k = docKind(name)
  return k === 'markdown' || k === 'text' ? 'note' : k
}

/** What to call an item: its document title, else its file name without the
 *  extension and with separators opened up ("fx_vol-notes.md" → "fx vol notes"). */
export function displayTitle(item: Pick<LibraryItem, 'name' | 'title'>): string {
  if (item.title.trim()) return item.title.trim()
  const stem = item.name.replace(/\.[^.]+$/, '')
  return stem.replace(/[_-]+/g, ' ').trim() || item.name
}

/** '' = every project, 'none' = saved outside a project, else a project id. */
export interface LibraryFilter {
  q: string
  group: LibraryGroup | 'all'
  project: string
}

function inProject(item: LibraryItem, project: string): boolean {
  if (!project) return true
  return project === 'none' ? item.project_id == null : item.project_id === project
}

function matches(item: LibraryItem, q: string): boolean {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return true
  const hay = `${item.title} ${item.name} ${item.project_name ?? ''}`.toLowerCase()
  return words.every((w) => hay.includes(w))
}

export function filterLibrary(items: LibraryItem[], f: LibraryFilter): LibraryItem[] {
  return items.filter(
    (it) =>
      inProject(it, f.project) && matches(it, f.q) && (f.group === 'all' || groupOf(it.name) === f.group),
  )
}

/** How many items each kind has under the search and project filter — the
 *  numbers on the kind buttons, which must not change as you click them. */
export function groupCounts(
  items: LibraryItem[],
  f: Pick<LibraryFilter, 'q' | 'project'>,
): Record<LibraryGroup | 'all', number> {
  const out: Record<LibraryGroup | 'all', number> = { all: 0, page: 0, pdf: 0, note: 0, image: 0, other: 0 }
  for (const it of filterLibrary(items, { ...f, group: 'all' })) {
    out.all++
    out[groupOf(it.name)]++
  }
  return out
}

/** The projects that hold something, in the order they first appear (newest
 *  file first), and whether anything sits outside a project. */
export function libraryProjects(items: LibraryItem[]): {
  projects: { id: string; name: string }[]
  loose: boolean
} {
  const seen = new Map<string, string>()
  let loose = false
  for (const it of items) {
    if (it.project_id == null) loose = true
    else if (!seen.has(it.project_id)) seen.set(it.project_id, it.project_name ?? 'Project')
  }
  return { projects: [...seen].map(([id, name]) => ({ id, name })), loose }
}
