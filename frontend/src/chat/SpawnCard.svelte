<script lang="ts">
  // Live card for an orchestrator-spawned background run (mcp__bran__spawn_agent).
  // Pulls the run_id out of the tool result and polls it so the user sees the
  // fire-and-forget run progress inline. When it finishes, the result can be
  // expanded right here — no need to leave the conversation.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration } from '../lib/time'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Icon from '../components/Icon.svelte'
  import Prose from '../components/Prose.svelte'
  import type { ToolItem } from './events'
  import type { RunRecord } from '../lib/types'

  let { tool }: { tool: ToolItem } = $props()

  const agentName = $derived((tool.input?.agent as string) ?? 'agent')
  const taskText = $derived((tool.input?.task as string) ?? '')
  const runId = $derived.by(() => {
    const m = tool.resultText?.match(/run_id=([0-9a-f-]{8,})/i)
    return m ? m[1] : null
  })

  let run = $state<RunRecord | null>(null)
  let expanded = $state(false)

  $effect(() => {
    const id = runId
    if (!id) return
    let timer: ReturnType<typeof setInterval> | undefined
    const poll = async () => {
      try {
        run = await api.run(id)
        if (run.status !== 'running' && run.status !== 'pending') clearInterval(timer)
      } catch {
        /* ignore transient errors */
      }
    }
    void poll()
    timer = setInterval(poll, 3000)
    return () => clearInterval(timer)
  })

  const status = $derived(run?.status ?? (runId ? 'running' : 'pending'))
  const done = $derived(status === 'completed' || status === 'failed' || status === 'cancelled')
  const hasOutput = $derived(done && !!(run?.result || run?.error))
</script>

<div class="card spawn-card">
  <div class="spawn-head">
    <span class="spawn-label"><Icon name="git-branch" size={14} />Background run</span>
    <span class="pill mute">{agentName}</span>
    <span class="ml-auto"><StatusBadge {status} /></span>
  </div>
  {#if taskText}
    <div class="spawn-task">{taskText}</div>
  {/if}

  <div class="spawn-foot">
    {#if hasOutput}
      <button class="btn-text" onclick={() => (expanded = !expanded)}>
        {expanded ? 'Hide result' : 'Show result'}
      </button>
    {/if}
    {#if runId}
      <a href={href('/runs/' + runId)} use:link class="spawn-link ml-auto">
        View run <span class="mono">{runId.slice(0, 8)}</span>
        <Icon name="arrow-right" size={12} />
      </a>
    {:else}
      <span class="text-muted" style="font-size: 12px;">Starting…</span>
    {/if}
  </div>

  {#if expanded && run}
    <div class="spawn-result">
      {#if run.error}
        <div style="color: var(--red); white-space: pre-wrap; font-size: 13px;">{run.error}</div>
      {:else if run.result}
        <Prose text={run.result} />
      {/if}
      <div class="spawn-meta">
        {fmtCost(run.total_cost_usd)} · {fmtDuration(run.duration_ms)} · {run.num_turns ?? '—'} turns
      </div>
    </div>
  {/if}
</div>

<style>
  .spawn-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .spawn-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .spawn-task {
    font-size: 13px;
    color: var(--fg-dim);
    margin-top: 6px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .spawn-foot {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
  }
  .spawn-link {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    font-weight: 500;
  }
  .spawn-link .mono {
    font-size: 11px;
  }
  .spawn-result {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .spawn-meta {
    margin-top: 10px;
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
</style>
