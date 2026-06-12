<script lang="ts">
  import { api, streamRunEvents } from '../lib/api'
  import { navigate, href, link } from '../lib/router.svelte'
  import { fmtBytes, fmtCost, fmtDuration, localDateTime } from '../lib/time'
  import Page from '../components/Page.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Prose from '../components/Prose.svelte'
  import ChatLog from '../chat/ChatLog.svelte'
  import { applyEvent, freshState, type ChatItem } from '../chat/events'
  import type { ArtifactEntry, RunRecord } from '../lib/types'

  let { runId }: { runId: string } = $props()

  let run = $state<RunRecord | null>(null)
  let transcript = $state<ChatItem[]>([])
  let artifacts = $state<ArtifactEntry[]>([])
  let error = $state<string | null>(null)
  let showTranscript = $state(false)

  // Most visits land here from Outputs to *read the answer* — so a finished
  // run leads with its result and tucks the transcript behind a toggle. A
  // live (or result-less) run still shows the transcript as the main view.
  const finished = $derived(run != null && run.status !== 'running' && run.status !== 'pending')
  const resultFirst = $derived(finished && !!(run?.result ?? '').trim())

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
    try {
      artifacts = await api.runArtifacts(runId)
    } catch {
      /* artifacts optional */
    }
  }

  $effect(() => {
    let timer: ReturnType<typeof setInterval> | undefined
    const aborter = new AbortController()
    void (async () => {
      try {
        run = await api.run(runId)
      } catch (e) {
        error = String(e)
        return
      }
      if (run.status === 'running' || run.status === 'pending') {
        // Live: stream the run's events into the transcript as it executes
        // (replay-then-live, see /spa/runs/{id}/stream). When the stream ends
        // the run finished — refetch the canonical record + transcript.
        void (async () => {
          transcript = []
          const st = freshState()
          try {
            for await (const ev of streamRunEvents(runId, aborter.signal)) {
              if (ev.type === 'done') break
              applyEvent(transcript, st, ev)
            }
          } catch {
            /* aborted on unmount, or stream unavailable — the poll covers status */
          }
          if (!aborter.signal.aborted) {
            run = await api.run(runId)
            await loadTranscript()
          }
        })()
        // Status/cost poll — also the fallback when the live stream isn't
        // available (e.g. the run predates a server restart).
        timer = setInterval(async () => {
          run = await api.run(runId)
          if (run.status !== 'running' && run.status !== 'pending') {
            clearInterval(timer)
            await loadTranscript()
          }
        }, 3000)
      } else {
        await loadTranscript()
      }
    })()
    return () => {
      clearInterval(timer)
      aborter.abort()
    }
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
        {#if artifacts.length}
          <div class="card-quiet meta-list" style="font-size: 12.5px;">
            <div class="label-cap" style="margin-bottom: 8px;">files produced</div>
            <div class="space-y-1">
              {#each artifacts as a (a.index)}
                {#if a.exists}
                  <div class="art-row">
                    <a href={'/spa/runs/' + encodeURIComponent(runId) + '/artifacts/' + a.index}
                       download class="mono text-accent-soft art-name" title={a.path}>{a.name}</a>
                    <span class="text-muted" style="font-size: 10px; white-space: nowrap;">{fmtBytes(a.size)}</span>
                  </div>
                {:else}
                  <div class="art-row">
                    <span class="mono text-muted art-name" title="{a.path} (deleted or moved since the run)" style="text-decoration: line-through;">{a.name}</span>
                  </div>
                {/if}
              {/each}
            </div>
          </div>
        {/if}
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
        {#if resultFirst}
          <div class="card"><div class="label-cap" style="margin-bottom: 6px;">result</div><div class="msg-prose"><Prose text={run.result ?? ''} /></div></div>
          {#if transcript.length}
            <!-- The transcript repeats the final answer at its tail; it's the
                 debug view, collapsed by default once the result is on top. -->
            <button class="transcript-toggle label-cap" onclick={() => (showTranscript = !showTranscript)}>
              {showTranscript ? '▾' : '▸'} transcript ({transcript.length} steps)
            </button>
            {#if showTranscript}
              <div class="space-y-3"><ChatLog items={transcript} /></div>
            {/if}
          {/if}
        {:else if transcript.length}
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
  .transcript-toggle {
    background: transparent;
    border: 0;
    padding: 4px 0;
    cursor: pointer;
    color: var(--muted);
    transition: color 0.12s var(--transition);
  }
  .transcript-toggle:hover { color: var(--accent-soft); }
  .art-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .art-name {
    font-size: 12px;
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  a.art-name:hover { text-decoration: underline; }
</style>
