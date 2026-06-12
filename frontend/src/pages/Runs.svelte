<script lang="ts">
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration, localDateTime, shortId } from '../lib/time'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import type { RunRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)

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
    try {
      runs = await api.runs({ limit: 200, exclude_chats: true })
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
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
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {:else if !runs.length}
    <div class="empty-state"><h3>no runs yet</h3></div>
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
    <div class="card flush" style="overflow-x: auto;">
      <table style="width: 100%; border-collapse: collapse; min-width: 720px;">
        <thead>
          <tr class="label-cap" style="text-align: left;">
            <th style="padding: 10px 14px;">ID</th><th>Agent</th><th>Source</th><th>Status</th><th>Started</th><th>Dur</th><th>Cost</th><th>Task</th>
          </tr>
        </thead>
        <tbody>
          {#each shown as r}
            <tr style="border-top: 1px solid var(--border);">
              <td style="padding: 8px 14px;">
                <a href={href('/runs/' + r.id)} use:link class="mono" style="color: var(--fg-dim); text-decoration: none;">{shortId(r.id)}</a>
                {#if r.parent_run_id}<span class="text-muted" title="spawned by another run" style="font-size: 10px;"> ↳</span>{/if}
              </td>
              <td class="text-bright">{r.agent}</td>
              <td><span class="src src-{r.source}">{r.source}</span></td>
              <td><StatusBadge status={r.status} /></td>
              <td class="text-dim" style="font-size: 12px;">{localDateTime(r.started_at)}</td>
              <td class="num text-dim">{fmtDuration(r.duration_ms)}</td>
              <td class="num text-dim">{fmtCost(r.total_cost_usd)}</td>
              <td class="text-dim" style="max-width: 320px;">
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
