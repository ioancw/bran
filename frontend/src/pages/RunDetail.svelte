<script lang="ts">
  import { api } from '../lib/api'
  import { navigate, href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration, localDateTime } from '../lib/time'
  import Page from '../components/Page.svelte'
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

<Page title={'Run ' + runId.slice(0, 8)}>
  {#snippet subtitle()}{run?.agent ?? ''}{/snippet}
  {#snippet actions()}
    {#if run}
      <StatusBadge status={run.status} />
      {#if run.status === 'running' || run.status === 'pending'}
        <button class="btn-outline" onclick={cancel}>⊘ cancel</button>
      {:else}
        <button class="btn-outline" onclick={rerun}>↻ re-run</button>
      {/if}
    {/if}
  {/snippet}

  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}
  {#if run}
    <div class="grid grid-cols-4 gap-6">
      <aside class="col-span-1 space-y-3">
        <div class="card-quiet">
          <div class="label-cap" style="margin-bottom: 6px;">task</div>
          <div class="text-fg" style="font-size: 13px; line-height: 1.5; white-space: pre-wrap;">{run.task}</div>
        </div>
        <div class="card-quiet meta-list" style="font-size: 12.5px;">
          <div class="label-cap" style="margin-bottom: 8px;">details</div>
          <div><span class="label-cap">turns</span> {run.num_turns ?? '—'}</div>
          <div><span class="label-cap">cost</span> {fmtCost(run.total_cost_usd)}</div>
          <div><span class="label-cap">duration</span> {fmtDuration(run.duration_ms)}</div>
          <div><span class="label-cap">started</span> {localDateTime(run.started_at)}</div>
          <div class="mono text-dim" style="word-break: break-all; margin-top: 6px;">session: {run.session_id ?? '—'}</div>
        </div>
        <div class="card-quiet meta-list" style="font-size: 12.5px;">
          <div class="label-cap" style="margin-bottom: 8px;">origin</div>
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
        {#if transcript.length}
          <!-- The transcript already ends with the final answer, so the
               separate result card below is only shown when there's no
               transcript (avoids rendering the result twice). -->
          <div class="label-cap">transcript</div>
          <div class="space-y-3"><ChatLog items={transcript} /></div>
        {:else if run.result}
          <div class="card"><div class="label-cap" style="margin-bottom: 6px;">result</div><div class="msg-prose"><Prose text={run.result} /></div></div>
        {/if}
      </div>
    </div>
  {/if}
</Page>

<style>
  /* Roomier label/value rows in the run's metadata column. */
  .meta-list > div { line-height: 1.85; }
  .meta-list .label-cap { margin-right: 7px; }
</style>
