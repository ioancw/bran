<script lang="ts">
  import { onReturn } from '../lib/refresh'
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration, localDateTime, shortId } from '../lib/time'
  import Page from '../components/Page.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Icon from '../components/Icon.svelte'
  import { sourceLabel, sourceHint } from '../lib/labels'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { toast } from '../lib/toast.svelte'
  import { errorText } from '../lib/errors'
  import type { RunRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  // Page size grows via "Show older" — no silent 200-row cliff.
  let limit = $state(200)
  let loadingMore = $state(false)
  const maybeMore = $derived(runs.length >= limit)

  // The Runs page is the fleet activity log — autonomous/managed executions.
  // Interactive chat turns live in Chat/Recents, so they're excluded here.
  // "All" is every run someone or something started. The steps those runs
  // handed to sub-agents are a separate view: one workflow records dozens, and
  // mixed in they bury the runs themselves. Each is also listed on its parent.
  const SOURCES = ['all', 'runner', 'spawn', 'manual', 'subagent'] as const
  type Source = (typeof SOURCES)[number]
  let filter = $state<Source>('all')
  const steps = $derived(filter === 'subagent')
  // True totals from the server; the table only holds the newest `limit`.
  let totals = $state<{ runs: number; subagent: number; by_source: Record<string, number> } | null>(null)
  const total = $derived(totals ? (steps ? totals.subagent : totals.runs) : runs.length)

  // Status filter — the #1 reason to open the activity log is "what failed?".
  // "running" folds in pending (both mean "not finished yet").
  const STATUSES = ['all', 'completed', 'failed', 'running', 'cancelled'] as const
  type Status = (typeof STATUSES)[number]
  let statusFilter = $state<Status>('all')
  const matchStatus = (r: RunRecord, s: Status) =>
    s === 'all' || (s === 'running' ? r.status === 'running' || r.status === 'pending' : r.status === s)

  const label = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

  const shown = $derived(
    runs.filter((r) => (filter === 'all' || r.source === filter) && matchStatus(r, statusFilter)),
  )
  // Each chip row counts within the *other* row's filter, so the numbers always
  // describe what clicking would show.
  // The two views load different rows, so a chip for the view you are not in
  // can only show the server's total (and only when no status narrows it).
  const countFor = (s: Source): number | null => {
    if ((s === 'subagent') !== steps) {
      if (!totals || statusFilter !== 'all') return null
      if (s === 'subagent') return totals.subagent
      return s === 'all' ? totals.runs : (totals.by_source[s] ?? 0)
    }
    return runs.filter((r) => (s === 'all' || r.source === s) && matchStatus(r, statusFilter)).length
  }
  const countForStatus = (s: Status) =>
    runs.filter((r) => (filter === 'all' || r.source === filter) && matchStatus(r, s)).length

  let seq = 0
  async function load() {
    const mine = ++seq
    const wantSteps = steps
    error = null // clear a stale banner so a recovered refresh shows clean
    try {
      const [rows, t] = await Promise.all([
        api.runs({ limit, exclude_chats: true, exclude_subagents: !wantSteps }),
        api.runCounts().catch(() => null),
      ])
      if (mine !== seq) return
      runs = wantSteps ? rows.filter((r) => r.source === 'subagent') : rows
      totals = t
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  async function loadMore() {
    if (loadingMore) return
    loadingMore = true
    limit += 200
    try {
      await load()
    } finally {
      loadingMore = false
    }
  }
  $effect(() => {
    void steps // switching between runs and sub-agent steps loads the other rows
    void load()
  })
  // Stay fresh: pick up runs that started/finished while the tab was unfocused.
  $effect(() => onReturn(() => void load()))

  async function removeRun(r: RunRecord) {
    const what = r.status === 'completed' ? 'this completed run and its output' : 'this run'
    const ok = await confirmDialog(
      `Delete ${what} (${shortId(r.id)})? It disappears from Runs and Outputs. Files it wrote stay on disk.`,
      'Delete run',
    )
    if (!ok) return
    try {
      await api.deleteRun(r.id)
      runs = runs.filter((x) => x.id !== r.id)
      toast('Run deleted', 'ok')
    } catch (e) {
      toast(errorText(e), 'err')
    }
  }
</script>

<Page title="Runs">
  {#snippet subtitle()}
    {#if shown.length === total}{total}
      {steps ? 'sub-agent steps' : total === 1 ? 'run' : 'runs'}{:else}Showing
      {shown.length} of {total}{steps ? ' sub-agent steps' : ''}{/if}
  {/snippet}
  {#if error}<ErrorBanner {error} onretry={load} />{/if}
  {#if !loaded}
    <Skeleton rows={5} />
  {:else if !runs.length && !steps && statusFilter === 'all'}
    <EmptyState
      title="No runs yet"
      hint="Every agent run lands here: from an automation, a background agent, or the command line."
      icon="history"
    />
  {:else}
    <div class="filters">
      <div class="seg" role="group" aria-label="Source">
        {#each SOURCES as s (s)}
          <button
            class="seg-btn"
            class:on={filter === s}
            aria-pressed={filter === s}
            onclick={() => (filter = s)}
          >
            {s === 'all' ? 'All' : s === 'subagent' ? 'Sub-agent steps' : sourceLabel(s)}
            {#if countFor(s) != null}<span class="seg-n">{countFor(s)}</span>{/if}
          </button>
        {/each}
      </div>
      <div class="seg" role="group" aria-label="Status">
        {#each STATUSES as s (s)}
          <button
            class="seg-btn"
            class:on={statusFilter === s}
            class:warn={s === 'failed'}
            aria-pressed={statusFilter === s}
            onclick={() => (statusFilter = s)}
          >
            {label(s)} <span class="seg-n">{countForStatus(s)}</span>
          </button>
        {/each}
      </div>
    </div>
    <!-- No sideways scroll: the Task column takes the room that is left, and
         the least important columns drop out as the page narrows. Every
         dropped field is on the run's own page. -->
    <div class="runs-wrap">
      <table class="bran-table">
        <thead>
          <tr>
            <th>ID</th><th class="c-agent">Agent</th><th class="c-src">Source</th><th>Status</th><th
              class="c-start">Started</th
            ><th class="num c-dur">Duration</th><th class="num c-cost">Cost</th><th>Task</th><th></th>
          </tr>
        </thead>
        <tbody>
          {#each shown as r (r.id)}
            <tr>
              <td>
                <span class="id-cell">
                  <a href={href('/runs/' + r.id)} use:link class="mono id-link">{shortId(r.id)}</a>
                  {#if r.parent_run_id}<span class="text-muted" title="Spawned by another run"
                      ><Icon name="corner-down-right" size={12} /></span
                    >{/if}
                </span>
              </td>
              <td class="c-agent" style="color: var(--fg-bright);">{r.agent}</td>
              <td class="c-src"
                ><span class="src src-{r.source}" title={sourceHint(r.source)}>{sourceLabel(r.source)}</span
                ></td
              >
              <td><StatusBadge status={r.status} /></td>
              <td class="text-dim c-start" style="white-space: nowrap;">{localDateTime(r.started_at)}</td>
              <td class="num mono c-dur">{fmtDuration(r.duration_ms)}</td>
              <td class="num mono c-cost">{fmtCost(r.total_cost_usd)}</td>
              <td class="text-dim task-cell">
                <div class="task-line">{r.task}</div>
                {#if r.status === 'failed' && (r.error_summary || r.error)}
                  <div class="task-err" title={r.error ?? ''}>{r.error_summary?.title ?? r.error}</div>
                  {#if r.error_summary?.hint}
                    <div class="task-hint">{r.error_summary.hint}</div>
                  {/if}
                {/if}
              </td>
              <td style="width: 36px;">
                {#if r.status !== 'running' && r.status !== 'pending'}
                  <button
                    class="btn-icon danger"
                    title="Delete run"
                    aria-label="Delete run {shortId(r.id)}"
                    onclick={() => removeRun(r)}><Icon name="x" size={14} /></button
                  >
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if maybeMore}
      <div style="text-align: center; margin-top: 14px;">
        <button class="btn-outline" disabled={loadingMore} onclick={loadMore}>
          {loadingMore ? 'Loading…' : 'Show older runs'}
        </button>
      </div>
    {/if}
  {/if}
</Page>

<style>
  .filters {
    display: flex;
    gap: 16px;
    margin-bottom: 14px;
    flex-wrap: wrap;
    align-items: center;
  }
  .runs-wrap {
    container-type: inline-size;
    overflow-x: auto; /* a last resort; the rules below keep the table inside */
  }
  /* The elastic column: takes what the others leave and truncates to it. */
  .task-cell {
    width: 100%;
    max-width: 0;
  }
  .c-agent {
    white-space: nowrap;
  }
  @container (max-width: 900px) {
    .c-dur,
    .c-cost {
      display: none;
    }
  }
  @container (max-width: 720px) {
    .c-src {
      display: none;
    }
  }
  @container (max-width: 560px) {
    .c-agent,
    .c-start {
      display: none;
    }
  }
  .id-cell {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .id-link {
    color: var(--fg-dim);
  }
  .task-line {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .task-err {
    color: var(--red);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .task-hint {
    color: var(--muted);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
