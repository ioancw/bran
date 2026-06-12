<script lang="ts">
  // Outputs: a reading surface for what your agents actually produced —
  // the result *text* of completed runner fires, spawns, and one-shots, grouped
  // by day and rendered inline. This is the "delivery" half of the agent-runner:
  // Runs is the activity *log* (a debug table); Outputs is where you read the
  // briefing without digging into a transcript.
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { relativeTime, localDateTime } from '../lib/time'
  import { errorText } from '../lib/errors'
  import { markOutputsSeen, isNewSince } from '../lib/seen.svelte'
  import Page from '../components/Page.svelte'
  import Prose from '../components/Prose.svelte'
  import type { RunRecord, ScheduleRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let runners = $state<ScheduleRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  let expanded = $state<Record<string, boolean>>({})
  let copiedId = $state<string | null>(null)

  // Opening this page is "reading your deliveries": advance the seen marker
  // (clears the sidebar badge) but keep the previous value so the cards that
  // were new at that moment stay highlighted for this visit.
  const seenBefore = markOutputsSeen()

  // Outputs come from autonomous/managed runs, never interactive chat turns.
  const SOURCES = ['all', 'runner', 'spawn', 'manual'] as const
  type Source = (typeof SOURCES)[number]
  let filter = $state<Source>('all')

  // A runner-fired output is titled by the *runner* (the thing you named),
  // not the agent that powers it.
  const runnerFor = (r: RunRecord): ScheduleRecord | null =>
    r.schedule_id ? (runners.find((s) => s.id === r.schedule_id) ?? null) : null
  const titleFor = (r: RunRecord): string => runnerFor(r)?.name ?? r.agent

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

  // Files the run produced (recorded in run metadata), as {index, name} for
  // download chips. Index keys the download endpoint; name is the basename.
  function artifactsOf(r: RunRecord): { index: number; name: string }[] {
    const raw = r.metadata?.artifacts
    if (!Array.isArray(raw)) return []
    return raw.map((p, index) => ({ index, name: String(p).split(/[\\/]/).pop() ?? String(p) }))
  }

  async function copyResult(r: RunRecord) {
    try {
      await navigator.clipboard.writeText(r.result ?? '')
      copiedId = r.id
      setTimeout(() => {
        if (copiedId === r.id) copiedId = null
      }, 1500)
    } catch {
      /* clipboard unavailable (http, permissions) — silently no-op */
    }
  }

  // "discuss →" hands the output to the orchestrator: open a new chat with the
  // composer pre-filled (?draft= — not auto-sent) so the user appends their
  // question; the agent pulls the full text itself via get_run_result.
  function discuss(r: RunRecord) {
    const draft =
      `Use get_run_result to read run ${r.id} — the "${titleFor(r)}" output from ` +
      `${relativeTime(r.started_at)}. I'd like to discuss it: `
    navigate('/chat?draft=' + encodeURIComponent(draft))
  }

  async function load() {
    try {
      ;[runs, runners] = await Promise.all([
        api.runs({ limit: 200, exclude_chats: true, status: 'completed' }),
        api.schedules(),
      ])
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void load()
  })
  // Stay fresh: a runner may fire while the tab sits in the background.
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
      <p style="font-size: 13px;"><a href={href('/runners')} use:link class="text-accent-soft" style="text-decoration: none;">create a runner →</a></p>
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
          {@const runner = runnerFor(r)}
          <article class="card output" class:fresh={isNewSince(r.started_at, seenBefore)}>
            <header class="out-head">
              {#if isNewSince(r.started_at, seenBefore)}<span class="new-dot" title="new since your last visit"></span>{/if}
              <a href={href('/runs/' + r.id)} use:link class="out-agent text-bright">{titleFor(r)}</a>
              <span class="src src-{r.source}">{r.source}</span>
              <span class="text-muted out-task" title={r.task}>{runner ? `${r.agent} · ${r.task}` : r.task}</span>
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
            <footer class="out-actions">
              {#each artifactsOf(r) as a (a.index)}
                <a href={'/spa/runs/' + encodeURIComponent(r.id) + '/artifacts/' + a.index}
                   download class="art-chip mono" title="download file produced by this run">📄 {a.name}</a>
              {/each}
              <button class="act" onclick={() => copyResult(r)}>{copiedId === r.id ? 'copied ✓' : 'copy'}</button>
              <button class="act" onclick={() => discuss(r)}>discuss →</button>
              {#if runner}
                <a href={href('/runners/' + encodeURIComponent(runner.name))} use:link class="act">runner →</a>
              {/if}
            </footer>
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
  .output.fresh { border-color: color-mix(in srgb, var(--accent-soft) 45%, var(--border)); }
  .new-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-soft);
    flex-shrink: 0;
    align-self: center;
  }
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

  .out-actions {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
    flex-wrap: wrap;
  }
  .art-chip {
    font-size: 11px;
    color: var(--accent-soft);
    text-decoration: none;
    border: 1px solid var(--border2);
    border-radius: 999px;
    padding: 2px 10px;
    transition: border-color 0.12s var(--transition);
  }
  .art-chip:hover { border-color: var(--accent-soft); }
  .act {
    background: transparent;
    border: 0;
    padding: 0;
    font-size: 11.5px;
    font-family: var(--font-mono);
    color: var(--muted);
    cursor: pointer;
    text-decoration: none;
    transition: color 0.12s var(--transition);
  }
  .act:hover { color: var(--accent-soft); }

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
