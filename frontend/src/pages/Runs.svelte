<script lang="ts">
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration, localDateTime, shortId } from '../lib/time'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import type { RunRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  // Page size grows via "load older" — no silent 200-row cliff.
  let limit = $state(200)
  let loadingMore = $state(false)
  const maybeMore = $derived(runs.length >= limit)

  // The Runs page is the fleet activity log — autonomous/managed executions.
  // Interactive chat turns live in Chat/Recents, so they're excluded here.
  const SOURCES = ['all', 'runner', 'spawn', 'manual'] as const
  type Source = (typeof SOURCES)[number]
  let filter = $state<Source>('all')

  // Status filter — the #1 reason to open the activity log is "what failed?".
  // "running" folds in pending (both mean "not finished yet").
  const STATUSES = ['all', 'completed', 'failed', 'running', 'cancelled'] as const
  type Status = (typeof STATUSES)[number]
  let statusFilter = $state<Status>('all')
  const matchStatus = (r: RunRecord, s: Status) =>
    s === 'all' || (s === 'running' ? r.status === 'running' || r.status === 'pending' : r.status === s)

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
  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <Skeleton rows={5} />
  {:else if !runs.length}
    <EmptyState title="no runs yet" hint="run an agent — from chat, a runner, or the CLI — and it lands here" />
  {:else}
    <div style="display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; align-items: center;">
      {#each SOURCES as s}
        <button class="chip" class:on={filter === s} onclick={() => (filter = s)}>
          {s} <span class="chip-n">{countFor(s)}</span>
        </button>
      {/each}
      <span class="chip-sep"></span>
      {#each STATUSES as s}
        <button class="chip" class:on={statusFilter === s} class:warn={s === 'failed'} onclick={() => (statusFilter = s)}>
          {s} <span class="chip-n">{countForStatus(s)}</span>
        </button>
      {/each}
    </div>
    <div style="overflow-x: auto;">
      <table class="bran-table" style="min-width: 720px;">
        <thead>
          <tr>
            <th>ID</th><th>Agent</th><th>Source</th><th>Status</th><th>Started</th><th class="num">Dur</th><th class="num">Cost</th><th>Task</th>
          </tr>
        </thead>
        <tbody>
          {#each shown as r}
            <tr>
              <td>
                <a href={href('/runs/' + r.id)} use:link class="mono" style="color: var(--fg-dim); text-decoration: none;">{shortId(r.id)}</a>
                {#if r.parent_run_id}<span class="text-muted" title="spawned by another run" style="font-size: 10px;"> ↳</span>{/if}
              </td>
              <td style="color: var(--fg-bright);">{r.agent}</td>
              <td><span class="src src-{r.source}">{r.source}</span></td>
              <td><StatusBadge status={r.status} /></td>
              <td style="color: var(--fg-dim); white-space: nowrap;">{localDateTime(r.started_at)}</td>
              <td class="num" style="color: var(--fg-dim);">{fmtDuration(r.duration_ms)}</td>
              <td class="num" style="color: var(--fg-dim);">{fmtCost(r.total_cost_usd)}</td>
              <td style="color: var(--fg-dim); max-width: 320px;">
                <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.task}</div>
                {#if r.status === 'failed' && r.error}
                  <div title={r.error} style="color: var(--red); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.error}</div>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if maybeMore}
      <div style="text-align: center; margin-top: 12px;">
        <button class="btn-outline" disabled={loadingMore} onclick={loadMore}>
          {loadingMore ? 'loading…' : 'load older →'}
        </button>
      </div>
    {/if}
  {/if}
</Page>

<style>
  .chip {
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 999px;
    padding: 4px 12px;
    font-size: 12px;
    color: var(--fg-dim);
    cursor: pointer;
    text-transform: capitalize;
    transition: all 0.12s var(--transition);
  }
  .chip:hover { color: var(--fg-bright); border-color: var(--muted); }
  .chip.on { background: var(--accent-glow); border-color: var(--accent-soft); color: var(--accent-soft); }
  .chip.warn.on {
    background: color-mix(in srgb, var(--red) 12%, transparent);
    border-color: var(--red);
    color: var(--red);
  }
  .chip-n { opacity: 0.6; font-family: var(--font-mono); font-size: 10px; }
  .chip-sep {
    width: 1px;
    height: 18px;
    background: var(--border2);
    margin: 0 4px;
  }
  /* .src / .src-* now live in global.css (shared source-pill component). */
</style>
