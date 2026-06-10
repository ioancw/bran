<script lang="ts">
  // Outputs: a reading surface for what your agents actually produced —
  // the result *text* of completed runner fires, spawns, and one-shots, grouped
  // by day and rendered inline. This is the "delivery" half of the agent-runner:
  // Runs is the activity *log* (a debug table); Outputs is where you read the
  // briefing without digging into a transcript.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { relativeTime, localDateTime } from '../lib/time'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import Prose from '../components/Prose.svelte'
  import type { RunRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  let expanded = $state<Record<string, boolean>>({})

  // Outputs come from autonomous/managed runs, never interactive chat turns.
  const SOURCES = ['all', 'runner', 'spawn', 'manual'] as const
  type Source = (typeof SOURCES)[number]
  let filter = $state<Source>('all')

  // Only completed runs that actually produced text are "outputs".
  const outputs = $derived(runs.filter((r) => r.status === 'completed' && (r.result ?? '').trim()))
  const shown = $derived(filter === 'all' ? outputs : outputs.filter((r) => r.source === filter))
  const countFor = (s: Source) =>
    s === 'all' ? outputs.length : outputs.filter((r) => r.source === s).length

  // Group by local day with friendly bucket labels (Today / Yesterday / date).
  function dayLabel(iso: string): string {
    const d = new Date(iso)
    const today = new Date()
    const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
    const diffDays = Math.round((startOf(today) - startOf(d)) / 86400000)
    if (diffDays === 0) return 'Today'
    if (diffDays === 1) return 'Yesterday'
    return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
  }
  const groups = $derived.by(() => {
    const out: { label: string; items: RunRecord[] }[] = []
    for (const r of shown) {
      const label = dayLabel(r.started_at)
      const last = out[out.length - 1]
      if (last && last.label === label) last.items.push(r)
      else out.push({ label, items: [r] })
    }
    return out
  })

  const isLong = (text: string) => (text ?? '').length > 700

  async function load() {
    try {
      runs = await api.runs({ limit: 200, exclude_chats: true, status: 'completed' })
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void load()
  })
</script>

<Page title="Outputs">
  {#snippet subtitle()}what your agents produced{/snippet}

  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {:else if !outputs.length}
    <div class="empty-state">
      <h3>no outputs yet</h3>
      <p class="text-muted" style="font-size: 13px;">
        When a runner fires or a background agent finishes, its result shows up here to read.
      </p>
    </div>
  {:else}
    <div style="display: flex; gap: 6px; margin-bottom: 18px; flex-wrap: wrap;">
      {#each SOURCES as s}
        <button class="chip" class:on={filter === s} onclick={() => (filter = s)}>
          {s} <span class="chip-n">{countFor(s)}</span>
        </button>
      {/each}
    </div>

    {#each groups as g}
      <div class="day-label label-cap">{g.label}</div>
      <div class="space-y-3" style="margin-bottom: 26px;">
        {#each g.items as r (r.id)}
          <article class="card output">
            <header class="out-head">
              <a href={href('/runs/' + r.id)} use:link class="out-agent text-bright">{r.agent}</a>
              <span class="src src-{r.source}">{r.source}</span>
              <span class="text-muted out-task" title={r.task}>{r.task}</span>
              <span class="ml-auto text-dim" style="font-size: 11px; white-space: nowrap;" title={localDateTime(r.started_at)}>{relativeTime(r.started_at)}</span>
            </header>
            <div class="out-body" class:clamp={isLong(r.result ?? '') && !expanded[r.id]}>
              <div class="msg-prose"><Prose text={r.result ?? ''} /></div>
            </div>
            {#if isLong(r.result ?? '')}
              <button class="more" onclick={() => (expanded = { ...expanded, [r.id]: !expanded[r.id] })}>
                {expanded[r.id] ? 'show less' : 'show more'}
              </button>
            {/if}
          </article>
        {/each}
      </div>
    {/each}
  {/if}
</Page>

<style>
  .day-label {
    color: var(--muted);
    margin: 0 0 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }
  .output { padding: 16px 18px; }
  .out-head {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 10px;
  }
  .out-agent { font-size: 14px; text-decoration: none; }
  .out-agent:hover { text-decoration: underline; }
  .out-task {
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 46ch;
  }
  /* Collapsed long outputs fade out; "show more" expands them in place. */
  .out-body { position: relative; }
  .out-body.clamp {
    max-height: 200px;
    overflow: hidden;
    -webkit-mask-image: linear-gradient(to bottom, #000 70%, transparent);
    mask-image: linear-gradient(to bottom, #000 70%, transparent);
  }
  .more {
    margin-top: 8px;
    background: transparent;
    border: 0;
    padding: 2px 0;
    font-size: 12px;
    color: var(--accent-soft);
    cursor: pointer;
  }
  .more:hover { text-decoration: underline; }

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
  .chip-n { opacity: 0.6; font-family: var(--font-mono); font-size: 10px; }
</style>
