<script lang="ts" module>
  // One shared ticker for every running tool row — a block subscribes while it
  // runs and unsubscribes when it finishes, so an idle transcript costs nothing.
  let tickNow = $state(performance.now())
  let subscribers = 0
  let ticker: ReturnType<typeof setInterval> | undefined
  function subscribe(): () => void {
    subscribers++
    if (!ticker) ticker = setInterval(() => (tickNow = performance.now()), 250)
    return () => {
      subscribers--
      if (subscribers <= 0 && ticker) {
        clearInterval(ticker)
        ticker = undefined
        subscribers = 0
      }
    }
  }
</script>

<script lang="ts">
  // Collapsible tool call + paired result.
  import { toolDisplayName, type ToolItem } from './events'
  import { category, preview, summary } from './tool-labels'
  import Icon from '../components/Icon.svelte'
  let { tool, open = false }: { tool: ToolItem; open?: boolean } = $props()

  // `open` is only the initial state; the chevron owns it afterwards.
  // svelte-ignore state_referenced_locally
  let expanded = $state(open)

  function lower(s: string): string {
    return (s || '').toLowerCase()
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
  const dotClass = $derived(
    tool.status === 'running' ? 'running' : tool.status === 'error' ? 'error' : 'done',
  )
  const rows = $derived(paramRows(tool.input))
  const isAgent = $derived(['agent', 'task'].includes(lower(tool.name)))

  // Live elapsed while running; the measured duration once finished.
  $effect(() => {
    if (tool.status !== 'running') return
    return subscribe()
  })
  const elapsed = $derived.by(() => {
    if (tool.status === 'running') return `${Math.max(0, (tickNow - tool.startedAt) / 1000).toFixed(1)}s`
    return tool.durationMs != null ? `${(tool.durationMs / 1000).toFixed(1)}s` : ''
  })
</script>

<div
  class="tool-block tool-cat-{cat}"
  class:running={tool.status === 'running'}
  class:errored={tool.isError}
  class:expanded
>
  <button type="button" class="tool-header" aria-expanded={expanded} onclick={() => (expanded = !expanded)}>
    <span class="tool-status-dot {dotClass}"></span>
    <span class="tool-name" title={tool.name}>{toolDisplayName(tool.name)}</span>
    <span class="tool-summary">{summary(tool.name, tool.input)}</span>
    <span class="tool-result-preview" class:error={tool.isError}
      >{preview(tool.name, tool.resultText, tool.isError)}</span
    >
    <span class="tool-duration">{elapsed}</span>
    <Icon name="chevron-down" size={12} class="tool-chevron" />
  </button>
  <div class="tool-body">
    {#if rows.length}
      <div class="tool-section">
        <div class="tool-section-label"><span>Parameters</span></div>
        <div class="tool-params">
          {#each rows as [k, v] (k)}
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
          <div class="tool-code" style="color: {tool.isError ? 'var(--red)' : 'inherit'};">
            {tool.resultText}
          </div>
        {/if}
      </div>
    {/if}
  </div>
</div>
