<script lang="ts">
  import { api } from '../lib/api'
  import { navigate, href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration, localDateTime } from '../lib/time'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Prose from '../components/Prose.svelte'
  import ChatLog from '../chat/ChatLog.svelte'
  import { applyEvent, freshState, type ChatItem } from '../chat/events'
  import type { RunRecord } from '../lib/types'

  let { runId }: { runId: string } = $props()

  let run = $state<RunRecord | null>(null)
  let transcript = $state<ChatItem[]>([])
  let error = $state<string | null>(null)

  async function loadTranscript() {
    try {
      const { events } = await api.runTranscript(runId)
      const items: ChatItem[] = []
      const st = freshState()
      for (const ev of events) applyEvent(items, st, ev)
      transcript = items
    } catch {
      /* transcript optional */
    }
  }

  $effect(() => {
    let timer: ReturnType<typeof setInterval> | undefined
    void (async () => {
      try {
        run = await api.run(runId)
        await loadTranscript()
        if (run.status === 'running' || run.status === 'pending') {
          timer = setInterval(async () => {
            run = await api.run(runId)
            if (run.status !== 'running' && run.status !== 'pending') {
              clearInterval(timer)
              await loadTranscript()
            }
          }, 3000)
        }
      } catch (e) {
        error = String(e)
      }
    })()
    return () => clearInterval(timer)
  })

  async function cancel() {
    await api.cancelRun(runId)
    run = await api.run(runId)
  }
  async function rerun() {
    if (!run) return
    const fresh = await api.newRun(run.agent, run.task)
    navigate('/runs/' + fresh.id)
  }
</script>

<header class="page-header">
  <h1>Run {runId.slice(0, 8)}</h1>
  <span class="subheading">{run?.agent ?? ''}</span>
  <div class="page-actions">
    {#if run}
      <StatusBadge status={run.status} />
      {#if run.status === 'running' || run.status === 'pending'}
        <button class="btn-outline" onclick={cancel}>⊘ cancel</button>
      {:else}
        <button class="btn-outline" onclick={rerun}>↻ re-run</button>
      {/if}
    {/if}
  </div>
</header>
<div class="px-8 py-6">
  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}
  {#if run}
    <div class="grid grid-cols-4 gap-6">
      <aside class="col-span-1 space-y-2">
        <div class="card-quiet">
          <div class="label-cap">task</div>
          <div class="text-fg" style="font-size: 13px; white-space: pre-wrap;">{run.task}</div>
        </div>
        <div class="card-quiet" style="font-size: 12px;">
          <div class="label-cap">details</div>
          <div>turns: {run.num_turns ?? '—'}</div>
          <div>cost: {fmtCost(run.total_cost_usd)}</div>
          <div>duration: {fmtDuration(run.duration_ms)}</div>
          <div>started: {localDateTime(run.started_at)}</div>
          <div class="mono text-dim" style="word-break: break-all;">session: {run.session_id ?? '—'}</div>
        </div>
        <div class="card-quiet" style="font-size: 12px;">
          <div class="label-cap">origin</div>
          {#if run.project_id}
            <div><a href={href('/projects/' + run.project_id)} use:link class="text-accent-soft" style="text-decoration: none;">↳ project</a></div>
          {:else}
            <div class="text-muted">standalone run</div>
          {/if}
          {#if run.session_id}
            <div><a href={href('/chat/' + encodeURIComponent(run.session_id))} use:link class="text-accent-soft" style="text-decoration: none;">↳ conversation</a></div>
          {/if}
        </div>
      </aside>
      <div class="col-span-3 space-y-3">
        {#if run.error}
          <div class="card" style="color: var(--red); white-space: pre-wrap; border-color: color-mix(in srgb, var(--red) 30%, transparent);">{run.error}</div>
        {/if}
        {#if run.result}
          <div class="card"><div class="label-cap" style="margin-bottom: 6px;">result</div><div class="msg-prose"><Prose text={run.result} /></div></div>
        {/if}
        {#if transcript.length}
          <div class="label-cap">transcript</div>
          <div class="space-y-3"><ChatLog items={transcript} /></div>
        {/if}
      </div>
    </div>
  {/if}
</div>
