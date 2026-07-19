<script lang="ts">
  // Live card for an orchestrator-spawned background run (mcp__bran__spawn_agent).
  // Pulls the run_id out of the tool result and polls it so the user sees the
  // fire-and-forget run progress inline. When it finishes, the result can be
  // expanded right here — no need to leave the conversation.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration } from '../lib/time'
  import StatusBadge from '../components/StatusBadge.svelte'
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
  <div style="display: flex; align-items: center; gap: 8px;">
    <span class="label-cap spawn-label" style="color: var(--amber);">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3v12a3 3 0 0 0 3 3h11"/><path d="M16 14l4 4-4 4"/><circle cx="6" cy="3" r="0.5" fill="currentColor"/></svg>
      background run
    </span>
    <span class="mono text-accent-soft" style="font-size: 11px;">{agentName}</span>
    <span class="ml-auto"><StatusBadge {status} /></span>
  </div>
  {#if taskText}
    <div class="text-dim" style="font-size: 13px; margin-top: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{taskText}</div>
  {/if}

  <div style="display: flex; align-items: center; gap: 12px; margin-top: 8px;">
    {#if hasOutput}
      <button class="btn-text" onclick={() => (expanded = !expanded)}>
        {expanded ? 'hide result ▴' : 'show result ▾'}
      </button>
    {/if}
    {#if runId}
      <a href={href('/runs/' + runId)} use:link class="text-accent-soft ml-auto" style="font-size: 11px; text-decoration: none;">view run {runId.slice(0, 8)} →</a>
    {:else}
      <span class="text-muted" style="font-size: 11px;">spawning…</span>
    {/if}
  </div>

  {#if expanded && run}
    <div class="spawn-result">
      {#if run.error}
        <div style="color: var(--red); white-space: pre-wrap; font-size: 13px;">{run.error}</div>
      {:else if run.result}
        <div class="msg-prose"><Prose text={run.result} /></div>
      {/if}
      <div class="label-cap" style="margin-top: 10px;">
        {fmtCost(run.total_cost_usd)} · {fmtDuration(run.duration_ms)} · {run.num_turns ?? '—'} turns
      </div>
    </div>
  {/if}
</div>

<style>
  .spawn-card {
    border-color: color-mix(in srgb, var(--amber) 35%, var(--border));
  }
  .spawn-label {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .spawn-result {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
</style>
