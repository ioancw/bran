<script lang="ts">
  import { api, streamRunEvents } from '../lib/api'
  import { toast } from '../lib/toast.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { errorText } from '../lib/errors'
  import { navigate, href, link } from '../lib/router.svelte'
  import { fmtBytes, fmtCost, fmtDuration, localDateTime } from '../lib/time'
  import { sourceHint, sourceLabel } from '../lib/labels'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Prose from '../components/Prose.svelte'
  import Icon from '../components/Icon.svelte'
  import ChatLog from '../chat/ChatLog.svelte'
  import Timeline from '../chat/Timeline.svelte'
  import { applyEvent, freshState, type ChatItem } from '../chat/events'
  import type { ArtifactEntry, RunRecord, SourceRef, Verification } from '../lib/types'
  import SourcesPanel from '../components/SourcesPanel.svelte'

  let { runId }: { runId: string } = $props()

  let run = $state<RunRecord | null>(null)
  // Inline subagents this run delegated to (recorded as child runs).
  let children = $state<RunRecord[]>([])
  // Where the answer came from (cited links + pages read, incl. subagents').
  let sources = $state<SourceRef[]>([])
  let transcript = $state<ChatItem[]>([])
  let artifacts = $state<ArtifactEntry[]>([])
  let error = $state<string | null>(null)
  let showTranscript = $state(false)
  let cancelling = $state(false)
  let showRawError = $state(false)
  let rerunning = $state(false)
  // Bumping this re-runs the load effect — the error banner's retry lever.
  let reloadTick = $state(0)
  function retry() {
    error = null
    reloadTick++
  }

  // Most visits land here from Outputs to *read the answer* — so a finished
  // run leads with its result and tucks the transcript behind a toggle. A
  // live (or result-less) run still shows the transcript as the main view.
  const finished = $derived(run != null && run.status !== 'running' && run.status !== 'pending')
  const resultFirst = $derived(finished && !!(run?.result ?? '').trim())

  // Evaluator verdict for verify-mode runner fires (metadata.verification).
  const verification = $derived.by((): Verification | null => {
    const v = run?.metadata?.verification
    return v && typeof v === 'object' ? (v as Verification) : null
  })

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
    void reloadTick // retry re-triggers the whole load/stream cycle
    let timer: ReturnType<typeof setInterval> | undefined
    const aborter = new AbortController()
    void (async () => {
      try {
        run = await api.run(runId)
      } catch (e) {
        error = String(e)
        return
      }
      api
        .runs({ parent_run_id: runId, limit: 50 })
        .then((r) => (children = r))
        .catch(() => {})
      api
        .runSources(runId)
        .then((r) => (sources = r))
        .catch(() => {})
      if (run.status === 'running' || run.status === 'pending') {
        // Live: stream the run's events into the transcript as it executes
        // (replay-then-live, see /spa/runs/{id}/stream). A dropped connection
        // mid-run reconnects with backoff — the server replays the buffer, so
        // resetting the transcript per attempt stays gap-free. When the stream
        // ends the run finished — refetch the canonical record + transcript.
        void (async () => {
          let attempts = 0
          while (!aborter.signal.aborted) {
            transcript = []
            const st = freshState()
            try {
              for await (const ev of streamRunEvents(runId, aborter.signal)) {
                if (ev.type === 'done') break
                attempts = 0 // healthy stream — reset the backoff
                applyEvent(transcript, st, ev)
              }
              break // clean end: the run finished (or isn't live in this process)
            } catch {
              if (aborter.signal.aborted) break
              // Transient drop. Reconnect only while the run is still going.
              try {
                run = await api.run(runId)
              } catch {
                break
              }
              if (run.status !== 'running' && run.status !== 'pending') break
              if (++attempts > 5) break // the 3s poll still covers status
              await new Promise((r) => setTimeout(r, Math.min(800 * 2 ** attempts, 8000)))
            }
          }
          if (!aborter.signal.aborted) {
            run = await api.run(runId)
            await loadTranscript()
          }
        })()
        // Status/cost poll — also the fallback when the live stream isn't
        // available (e.g. the run predates a server restart). Tolerate transient
        // fetch failures (server restart mid-run); give up after several so a
        // dead server doesn't throw every 3s forever.
        let pollFails = 0
        timer = setInterval(async () => {
          try {
            run = await api.run(runId)
            pollFails = 0
          } catch {
            if (++pollFails > 5) clearInterval(timer)
            return
          }
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
    if (cancelling) return
    cancelling = true
    try {
      await api.cancelRun(runId)
      run = await api.run(runId)
    } catch (e) {
      toast(errorText(e), 'err')
    } finally {
      cancelling = false
    }
  }
  let deleting = $state(false)
  async function remove() {
    if (!run || deleting) return
    const ok = await confirmDialog(
      `Delete run ${runId.slice(0, 8)}? It disappears from Runs and Outputs. Files it wrote stay on disk.`,
      'Delete run',
    )
    if (!ok) return
    deleting = true
    try {
      await api.deleteRun(runId)
      toast('Run deleted', 'ok')
      navigate('/runs')
    } catch (e) {
      toast(errorText(e), 'err')
    } finally {
      deleting = false
    }
  }
  async function rerun() {
    if (!run || rerunning) return
    rerunning = true
    try {
      const fresh = await api.newRun(run.agent, run.task)
      navigate('/runs/' + fresh.id)
    } catch (e) {
      toast(errorText(e), 'err')
    } finally {
      rerunning = false
    }
  }
</script>

<Page title={'Run ' + runId.slice(0, 8)}>
  {#snippet subtitle()}{run?.agent ?? ''}{/snippet}
  {#snippet actions()}
    {#if run}
      <StatusBadge status={run.status} />
      {#if run.status === 'running' || run.status === 'pending'}
        <button class="btn-outline" disabled={cancelling} onclick={cancel}>
          <Icon name="stop" size={13} />{cancelling ? 'Cancelling…' : 'Cancel'}
        </button>
      {:else}
        <button class="btn-outline" disabled={rerunning} onclick={rerun}>
          <Icon name="refresh" size={13} />{rerunning ? 'Starting…' : 'Re-run'}
        </button>
        <button class="btn-ghost danger" disabled={deleting} onclick={remove} title="Delete this run">
          <Icon name="trash" size={13} />Delete
        </button>
      {/if}
    {/if}
  {/snippet}

  {#if error}
    <ErrorBanner {error} onretry={retry} />
  {:else if !run}
    <Skeleton rows={6} />
  {/if}
  {#if run}
    <div class="grid grid-cols-4 gap-6">
      <aside class="col-span-1 space-y-3">
        <div class="card">
          <h2 class="card-title">Task</h2>
          <div class="task-text">{run.task}</div>
        </div>
        <div class="card">
          <h2 class="card-title">Details</h2>
          <dl class="defs">
            <dt>Turns</dt>
            <dd>{run.num_turns ?? '—'}</dd>
            <dt>Cost</dt>
            <dd class="mono">{fmtCost(run.total_cost_usd)}</dd>
            <dt>Duration</dt>
            <dd class="mono">{fmtDuration(run.duration_ms)}</dd>
            <dt>Started</dt>
            <dd>{localDateTime(run.started_at)}</dd>
            {#if run.actor}
              <dt>Triggered by</dt>
              <dd>{run.actor}</dd>
            {/if}
            <dt>Session</dt>
            <dd class="mono session">{run.session_id ?? '—'}</dd>
          </dl>
        </div>
        {#if verification}
          <div class="card">
            <h2 class="card-title">Verification</h2>
            {#if verification.status === 'skipped'}
              <div class="verif-line text-muted">
                <Icon name="minus" size={14} />Skipped: {verification.reason ?? 'reviewer unavailable'}
              </div>
            {:else if verification.pass}
              <div class="verif-line" style="color: var(--green);">
                <Icon name="circle-check" size={14} />Passed review
              </div>
            {:else}
              <div class="verif-line" style="color: var(--amber);">
                <Icon name="alert-circle" size={14} />Reviewer found issues
              </div>
              {#if verification.issues?.length}
                <ul class="verif-issues">
                  {#each verification.issues as issue, i (i)}<li>{issue}</li>{/each}
                </ul>
              {/if}
              {#if verification.retried_run_id}
                <a href={href('/runs/' + verification.retried_run_id)} use:link class="verif-link">
                  <Icon name="corner-down-right" size={13} />Corrected attempt
                </a>
              {/if}
            {/if}
            {#if verification.retry_of}
              <a href={href('/runs/' + verification.retry_of)} use:link class="verif-link">
                <Icon name="corner-down-right" size={13} />Original attempt
              </a>
            {/if}
          </div>
        {/if}
        {#if artifacts.length}
          <div class="card">
            <h2 class="card-title">Files produced</h2>
            <div class="space-y-1">
              {#each artifacts as a (a.index)}
                {#if a.exists}
                  <div class="art-row">
                    <Icon name="file-text" size={14} class="text-muted" />
                    <a
                      href={'/spa/runs/' + encodeURIComponent(runId) + '/artifacts/' + a.index}
                      download
                      class="mono art-name"
                      title={a.path}>{a.name}</a
                    >
                    <span class="text-muted art-size">{fmtBytes(a.size)}</span>
                  </div>
                {:else}
                  <div class="art-row">
                    <Icon name="file" size={14} class="text-muted" />
                    <span
                      class="mono text-muted art-name"
                      title="{a.path} (deleted or moved since the run)"
                      style="text-decoration: line-through;">{a.name}</span
                    >
                  </div>
                {/if}
              {/each}
            </div>
          </div>
        {/if}
        <div class="card">
          <h2 class="card-title">Origin</h2>
          <div class="space-y-1">
            {#if run.project_id}
              <a href={href('/projects/' + run.project_id)} use:link class="origin-link">
                <Icon name="folder" size={14} />Project
              </a>
            {:else}
              <div class="text-muted" style="font-size: 13px;">Standalone run</div>
            {/if}
            {#if run.session_id}
              <a href={href('/chat/' + encodeURIComponent(run.session_id))} use:link class="origin-link">
                <Icon name="message-square" size={14} />Conversation
              </a>
            {/if}
          </div>
        </div>
        {#if sources.length}
          <div class="card">
            <h2 class="card-title">Sources <span class="title-count">{sources.length}</span></h2>
            <SourcesPanel {sources} />
          </div>
        {/if}
        {#if children.length}
          <div class="card">
            <h2 class="card-title">Delegated work</h2>
            <div class="space-y-1">
              {#each children as c (c.id)}
                <a href={href('/runs/' + c.id)} use:link class="origin-link child-row">
                  <span class="src src-{c.source}" title={sourceHint(c.source)}>{sourceLabel(c.source)}</span>
                  <span class="child-agent">{c.agent}</span>
                  {#if c.duration_ms != null}<span class="child-dur">{fmtDuration(c.duration_ms)}</span>{/if}
                  <StatusBadge status={c.status} />
                </a>
              {/each}
            </div>
          </div>
        {/if}
      </aside>
      <div class="col-span-3 space-y-3">
        {#if run.error_summary && (run.status === 'failed' || run.status === 'cancelled')}
          <div class="card wrong-card" class:cancelled={run.status === 'cancelled'}>
            <h2 class="card-title">What went wrong</h2>
            <div class="wrong-title">{run.error_summary.title}</div>
            {#if run.error_summary.hint}<p class="wrong-hint">{run.error_summary.hint}</p>{/if}
            {#if run.error}
              <button
                class="btn-text"
                aria-expanded={showRawError}
                onclick={() => (showRawError = !showRawError)}
              >
                {showRawError ? 'Hide details' : 'Show details'}
              </button>
              {#if showRawError}<div class="tool-code full wrong-raw">{run.error}</div>{/if}
            {/if}
          </div>
        {:else if run.error}
          <div class="card err-card">{run.error}</div>
        {/if}
        {#if resultFirst}
          <div class="card">
            <h2 class="card-title">Result</h2>
            <div class="reading-col"><Prose text={run.result ?? ''} {sources} /></div>
          </div>
          {#if transcript.length}
            <div class="card">
              <h2 class="card-title">
                Timeline <span class="title-count">{transcript.length} steps</span>
              </h2>
              <Timeline items={transcript} />
            </div>
            <!-- The transcript repeats the final answer at its tail; it's the
                 long form, collapsed by default once the result is on top. -->
            <button
              class="transcript-toggle"
              aria-expanded={showTranscript}
              onclick={() => (showTranscript = !showTranscript)}
            >
              <Icon name={showTranscript ? 'chevron-down' : 'chevron-right'} size={14} />
              Full transcript
            </button>
            {#if showTranscript}
              <div class="space-y-3"><ChatLog items={transcript} /></div>
            {/if}
          {/if}
        {:else if transcript.length}
          <div class="card">
            <h2 class="card-title">
              Timeline <span class="title-count">{transcript.length} steps</span>
            </h2>
            <Timeline items={transcript} />
          </div>
          <button
            class="transcript-toggle"
            aria-expanded={!showTranscript}
            onclick={() => (showTranscript = !showTranscript)}
          >
            <Icon name={!showTranscript ? 'chevron-down' : 'chevron-right'} size={14} />
            Full transcript
          </button>
          {#if !showTranscript}
            <div class="space-y-3"><ChatLog items={transcript} /></div>
          {/if}
        {:else if run.result}
          <div class="card">
            <h2 class="card-title">Result</h2>
            <div class="reading-col"><Prose text={run.result} {sources} /></div>
          </div>
        {/if}
      </div>
    </div>
  {/if}
</Page>

<style>
  .title-count {
    font-weight: 400;
    color: var(--muted);
    margin-left: 6px;
  }
  .child-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .child-agent {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .card-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg-bright);
    margin-bottom: 10px;
  }
  .task-text {
    font-size: 13px;
    line-height: 1.5;
    color: var(--fg);
    white-space: pre-wrap;
  }
  .defs {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 6px 12px;
    margin: 0;
    font-size: 13px;
  }
  .defs dt {
    font-size: 12px;
    color: var(--muted);
    padding-top: 1px;
  }
  .defs dd {
    margin: 0;
    min-width: 0;
    color: var(--fg);
  }
  .defs .mono {
    font-size: 12.5px;
  }
  .defs .session {
    font-size: 11.5px;
    color: var(--fg-dim);
    word-break: break-all;
  }

  /* The briefing is the product: the result reads like print, in a column. */
  .reading-col {
    max-width: var(--chat-col);
  }
  .reading-col :global(.prose-md) {
    font-family: var(--font-prose);
    font-size: 16.5px;
    line-height: 1.7;
  }
  .wrong-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--red);
  }
  .wrong-card.cancelled .wrong-title {
    color: var(--fg-dim);
  }
  .wrong-hint {
    margin: 4px 0 8px;
    font-size: 13px;
    color: var(--fg-dim);
    line-height: 1.5;
  }
  .wrong-raw {
    margin-top: 8px;
  }
  .child-dur {
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .err-card {
    color: var(--red);
    white-space: pre-wrap;
    font-size: 13px;
    background: var(--red-soft);
    border-color: color-mix(in srgb, var(--red) 30%, transparent);
  }

  .transcript-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: transparent;
    border: 0;
    padding: 4px 0;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-dim);
    transition: color var(--dur-1) var(--transition);
  }
  .transcript-toggle:hover {
    color: var(--fg-bright);
  }

  .verif-line {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 500;
  }
  .verif-issues {
    margin: 6px 0 0;
    padding-left: 18px;
    color: var(--fg-dim);
    font-size: 12.5px;
    line-height: 1.6;
  }
  .verif-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-top: 8px;
    font-size: 13px;
  }

  .art-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
  }
  .art-name {
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .art-size {
    font-size: 11px;
    white-space: nowrap;
    margin-left: auto;
  }
  .origin-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
  }
</style>
