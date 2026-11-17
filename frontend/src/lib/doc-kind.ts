// How a file can be shown inside the app, by its extension.

export type DocKind = 'page' | 'pdf' | 'markdown' | 'text' | 'image' | 'other'

const TEXT_EXT = new Set([
  'txt',
  'csv',
  'tsv',
  'json',
  'log',
  'tex',
  'py',
  'js',
  'ts',
  'yaml',
  'yml',
  'toml',
  'xml',
  'ini',
  'sql',
  'sh',
])
const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'])

export function docKind(name: string): DocKind {
  const ext = name.toLowerCase().split('.').pop() ?? ''
  if (ext === 'html' || ext === 'htm') return 'page'
  if (ext === 'pdf') return 'pdf'
  if (ext === 'md' || ext === 'markdown') return 'markdown'
  if (TEXT_EXT.has(ext)) return 'text'
  if (IMAGE_EXT.has(ext)) return 'image'
  return 'other'
}
