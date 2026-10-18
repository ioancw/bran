<script lang="ts" module>
  // Shared 250ms ticker for the live elapsed time (same pattern as ToolBlock:
  // subscribe while running, nothing runs when the transcript is idle).
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
  // A delegation card: the orchestrator handed a focused subtask to an inline
  // subagent through the Agent tool. bran records that as a child run, so the
  // card links to it; the result shows here once the subagent returns.
  import { href, link } from '../lib/router.svelte'
  import Icon from '../components/Icon.svelte'
  import type { ToolItem } from './events'

  let { tool }: { tool: ToolItem } = $props()

  const isWorkflow = $derived(tool.name === 'Workflow')
  const workflowName = $derived.by(() => {
    const named = tool.input?.name as string | undefined
    if (named) return named
    const script = String(tool.input?.script ?? '')
    const m = script.match(/name\s*:\s*['"]([^'"]{1,80})['"]/)
    return m ? m[1] : 'workflow'
  })
  const agentName = $derived(
    isWorkflow ? workflowName : (tool.input?.subagent_type as string) || 'general-purpose',
  )
  const prompt = $derived(((tool.input?.prompt ?? tool.input?.description) as string) ?? '')
  const RESULT_PREVIEW = 400
  let showPrompt = $state(false)
  let showResult = $state(false)
  const longPrompt = $derived(prompt.length > 240)
  const result = $derived(tool.resultText ?? '')
  const resultShort = $derived(
    result.length > RESULT_PREVIEW ? result.slice(0, RESULT_PREVIEW).trimEnd() + '…' : result,
  )

  $effect(() => {
    if (tool.status !== 'running') return
    return subscribe()
  })
  const elapsed = $derived.by(() => {
    if (tool.status === 'running') return `${Math.max(0, (tickNow - tool.startedAt) / 1000).toFixed(1)}s`
    return tool.durationMs != null ? `${(tool.durationMs / 1000).toFixed(1)}s` : ''
  })
  const statusWord = $derived(
    tool.status === 'running' ? 'working…' : tool.status === 'error' ? 'failed' : 'done',
  )
  const dotClass = $derived(tool.status === 'running' ? 'warn pulse' : tool.status === 'error' ? 'err' : 'ok')
</script>

<div class="card sub-card" class:errored={tool.status === 'error'}>
  <div class="sub-head">
    <span class="sub-title"
      ><Icon name="bot" size={14} />{isWorkflow ? 'Running workflow' : 'Delegated to'}
      <strong>{agentName}</strong></span
    >
    <span class="sub-status">
      <span class="dot {dotClass}"></span>
      <span>{statusWord}</span>
      {#if elapsed}<span class="sub-elapsed">{elapsed}</span>{/if}
    </span>
    {#if tool.runId}
      <a href={href('/runs/' + tool.runId)} use:link class="btn-text ml-auto">Open run</a>
    {/if}
  </div>
  {#if prompt}
    <div class="sub-prompt" class:clamped={longPrompt && !showPrompt}>{prompt}</div>
    {#if longPrompt}
      <button class="btn-text sub-more" onclick={() => (showPrompt = !showPrompt)}>
        {showPrompt ? 'Show less' : 'Show more'}
      </button>
    {/if}
  {/if}
  {#if tool.status !== 'running' && result}
    <div class="sub-result">
      <div class="sub-result-label">{tool.status === 'error' ? 'Error' : 'Result'}</div>
      <div class="sub-result-body">{showResult ? result : resultShort}</div>
      {#if result.length > RESULT_PREVIEW}
        <button class="btn-text sub-more" onclick={() => (showResult = !showResult)}>
          {showResult ? 'Show less' : 'Show more'}
        </button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .sub-card {
    padding: 12px;
  }
  .sub-card.errored {
    background: var(--red-soft);
    border-color: color-mix(in srgb, var(--red) 30%, transparent);
  }
  .sub-head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .sub-title {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--fg-dim);
  }
  .sub-title strong {
    color: var(--fg-bright);
    font-weight: 600;
  }
  .sub-status {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--fg-dim);
  }
  .sub-elapsed {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .pulse {
    animation: brn-pulse 1.6s ease-in-out infinite;
  }
  .sub-prompt {
    margin-top: 8px;
    font-size: 13px;
    line-height: 1.5;
    color: var(--fg-dim);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .sub-prompt.clamped {
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .sub-more {
    margin-top: 4px;
  }
  .sub-result {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .sub-result-label {
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .sub-result-body {
    font-size: 13px;
    line-height: 1.55;
    color: var(--fg);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
