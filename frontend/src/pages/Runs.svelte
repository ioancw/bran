<script lang="ts">
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration, localDateTime, shortId } from '../lib/time'
  import Page from '../components/Page.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import Icon from '../components/Icon.svelte'
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
  const SOURCES = ['all', 'runner', 'spawn', 'subagent', 'manual'] as const
  type Source = (typeof SOURCES)[number]
  let filter = $state<Source>('all')

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
  const countFor = (s: Source) =>
    runs.filter((r) => (s === 'all' || r.source === s) && matchStatus(r, statusFilter)).length
  const countForStatus = (s: Status) =>
    runs.filter((r) => (filter === 'all' || r.source === filter) && matchStatus(r, s)).length

  async function load() {
    error = null // clear a stale banner so a recovered refresh shows clean
    try {
      runs = await api.runs({ limit, exclude_chats: true })
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
    void load()
  })
  // Stay fresh: pick up runs that started/finished while the tab was unfocused.
  $effect(() => {
    const refresh = () => {
      if (document.visibilityState === 'visible') void load()
    }
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  })
</script>

<Page title="Runs">
  {#snippet subtitle()}{shown.length} of {runs.length}{/snippet}
  {#if error}<ErrorBanner {error} onretry={load} />{/if}
  {#if !loaded}
    <Skeleton rows={5} />
  {:else if !runs.length}
    <EmptyState
      title="No runs yet"
      hint="Every agent run lands here: from chat, from a runner, or from the command line."
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
            {label(s)} <span class="seg-n">{countFor(s)}</span>
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
    <div style="overflow-x: auto;">
      <table class="bran-table" style="min-width: 760px;">
        <thead>
          <tr>
            <th>ID</th><th>Agent</th><th>Source</th><th>Status</th><th>Started</th><th class="num"
              >Duration</th
            ><th class="num">Cost</th><th>Task</th>
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
              <td style="color: var(--fg-bright);">{r.agent}</td>
              <td><span class="src src-{r.source}">{r.source}</span></td>
              <td><StatusBadge status={r.status} /></td>
              <td class="text-dim" style="white-space: nowrap;">{localDateTime(r.started_at)}</td>
              <td class="num mono">{fmtDuration(r.duration_ms)}</td>
              <td class="num mono">{fmtCost(r.total_cost_usd)}</td>
              <td class="text-dim" style="max-width: 320px;">
                <div class="task-line">{r.task}</div>
                {#if r.status === 'failed' && r.error}
                  <div class="task-err" title={r.error}>{r.error}</div>
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
  .seg {
    display: inline-flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  .seg-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    height: 26px;
    padding: 0 9px;
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    font-size: 12px;
    font-weight: 500;
    color: var(--fg-dim);
    cursor: pointer;
    transition:
      background var(--dur-1) var(--transition),
      color var(--dur-1) var(--transition),
      border-color var(--dur-1) var(--transition);
  }
  .seg-btn:hover {
    background: var(--surface2);
    color: var(--fg-bright);
  }
  .seg-btn.on {
    background: var(--accent-glow);
    border-color: transparent;
    color: var(--accent-soft);
  }
  .seg-btn.warn.on {
    background: var(--red-soft);
    color: var(--red);
  }
  .seg-n {
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .seg-btn.on .seg-n {
    color: inherit;
    opacity: 0.75;
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
</style>
