<script lang="ts">
  // Collapsible tool call + paired result, ported from chat.html's tool block.
  import { toolDisplayName, type ToolItem } from './events'
  let { tool }: { tool: ToolItem } = $props()

  let expanded = $state(false)

  function lower(s: string): string {
    return (s || '').toLowerCase()
  }
  function category(name: string): string {
    const n = lower(name)
    if (['read', 'glob', 'grep', 'ls'].includes(n)) return 'search'
    if (['edit', 'write', 'multiedit', 'notebookedit'].includes(n)) return 'write'
    if (['bash', 'powershell'].includes(n)) return 'shell'
    if (n.startsWith('web') || n.startsWith('mcp__tavily')) return 'web'
    if (n === 'agent' || n === 'task' || n.startsWith('mcp__bran__spawn')) return 'agent'
    if (n.includes('fetch') || n.includes('search')) return 'web'
    return 'misc'
  }
  function shortPath(p: unknown): string {
    if (!p) return ''
    const parts = String(p).replace(/\\/g, '/').split('/').filter(Boolean)
    return parts.length <= 3 ? parts.join('/') : '…/' + parts.slice(-2).join('/')
  }
  function truncate(s: unknown, max: number): string {
    const str = String(s ?? '')
    return str.length > max ? str.slice(0, max - 1) + '…' : str
  }
  function summary(name: string, input: Record<string, unknown>): string {
    const n = lower(name)
    const i = input || {}
    if (['read', 'edit', 'write'].includes(n)) return i.file_path ? shortPath(i.file_path) : ''
    if (['bash', 'powershell'].includes(n)) return i.command ? truncate(i.command, 60) : ''
    if (n === 'grep' || n === 'glob') return i.pattern ? truncate(i.pattern, 40) : ''
    if (n === 'agent' || n === 'task') {
      const sub = (i.subagent_type as string) || 'subagent'
      const desc = i.description ? ` — ${truncate(i.description, 40)}` : ''
      return `${sub}${desc}`
    }
    if (n === 'websearch' || n === 'mcp__tavily__tavily_search') return i.query ? truncate(i.query, 60) : ''
    if (n === 'webfetch' || n === 'mcp__tavily__tavily_extract') return i.url ? truncate(i.url, 60) : ''
    // Unknown tools (MCP etc.): surface the most informative string param, so
    // "fetch_url  https://feeds.bbci.co.uk/…" instead of a bare name.
    for (const k of ['url', 'query', 'file_path', 'path', 'pattern', 'command', 'task', 'name', 'prompt', 'text']) {
      const v = i[k]
      if (typeof v === 'string' && v.trim()) return truncate(v, 60)
    }
    return ''
  }
  function preview(name: string, text: string, isError: boolean): string {
    if (isError) return 'error'
    if (!text) return ''
    const n = lower(name)
    if (n === 'read') return `${text.split('\n').length} lines`
    if (n === 'grep') return `${text.split('\n').filter(Boolean).length} matches`
    if (n === 'glob') return `${text.split('\n').filter(Boolean).length} files`
    if (n === 'bash' || n === 'powershell') return text.toLowerCase().includes('error') ? 'with errors' : 'ok'
    if (n === 'edit' || n === 'write') return 'saved'
    if (n === 'agent' || n === 'task') return 'done'
    return 'ok'
  }
  function paramRows(input: Record<string, unknown>): [string, string][] {
    if (!input || typeof input !== 'object') return []
    return Object.entries(input).map(([k, v]) => {
      let display: string
      if (v === null || v === undefined) display = ''
      else if (typeof v === 'string') display = v
      else {
        try {
          display = JSON.stringify(v)
        } catch {
          display = String(v)
        }
      }
      return [k, display]
    })
  }

  const cat = $derived(category(tool.name))
  const dotClass = $derived(tool.status === 'running' ? 'running' : tool.status === 'error' ? 'error' : 'done')
  const rows = $derived(paramRows(tool.input))
  const isAgent = $derived(['agent', 'task'].includes(lower(tool.name)))
</script>

<div class="tool-block tool-cat-{cat}" class:running={tool.status === 'running'} class:errored={tool.isError} class:expanded>
  <button type="button" class="tool-header" onclick={() => (expanded = !expanded)}>
    <span class="tool-status-dot {dotClass}"></span>
    <span class="tool-name" title={tool.name}>{toolDisplayName(tool.name)}</span>
    <span class="tool-summary">{summary(tool.name, tool.input)}</span>
    <span class="tool-result-preview" class:error={tool.isError}>{preview(tool.name, tool.resultText, tool.isError)}</span>
    <span class="tool-duration">{tool.durationMs != null ? `${(tool.durationMs / 1000).toFixed(1)}s` : ''}</span>
    <svg class="tool-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
      <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </button>
  <div class="tool-body">
    {#if rows.length}
      <div class="tool-section">
        <div class="tool-section-label"><span>Params</span></div>
        <div class="tool-params">
          {#each rows as [k, v]}
            <span class="tool-param-key">{k}</span><span class="tool-param-value">{v}</span>
          {/each}
        </div>
      </div>
    {/if}
    {#if tool.status !== 'running' && tool.resultText}
      <div class="tool-section">
        <div class="tool-section-label">
          <span>{tool.isError ? 'Error' : isAgent ? 'Response' : 'Output'}</span>
        </div>
        {#if isAgent}
          <div class="tool-task-result">{tool.resultText}</div>
        {:else}
          <div class="tool-code" style="color: {tool.isError ? 'var(--red)' : 'inherit'};">{tool.resultText}</div>
        {/if}
      </div>
    {/if}
  </div>
</div>
