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
  import { bumpActivity } from '../lib/workspace.svelte'
  import { isAlertResult, isSuperseded, verifyBadge } from '../lib/verification'
  import { toast } from '../lib/toast.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import Prose from '../components/Prose.svelte'
  import type { RunRecord, ScheduleRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let runners = $state<ScheduleRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  let expanded = $state<Record<string, boolean>>({})

  // Free-text search (server-side, over every completed output) + a starred-only
  // view. Read/star state is durable + cross-browser (server output_state).
  let q = $state('')
  let starredOnly = $state(false)

  // Which outputs were unread when this view loaded — kept so the cards that
  // were new at that moment stay highlighted for the visit, even though we mark
  // them read on the server straight away (which clears the sidebar badge).
  let freshIds = $state<Set<string>>(new Set())

  // Outputs come from autonomous/managed runs, never interactive chat turns.
  const SOURCES = ['all', 'runner', 'spawn', 'manual'] as const
  type Source = (typeof SOURCES)[number]
  let filter = $state<Source>('all')

  // A runner-fired output is titled by the *runner* (the thing you named),
  // not the agent that powers it.
  const runnerFor = (r: RunRecord): ScheduleRecord | null =>
    r.schedule_id ? (runners.find((s) => s.id === r.schedule_id) ?? null) : null
  const titleFor = (r: RunRecord): string => runnerFor(r)?.name ?? r.agent

  // Only completed runs that actually produced text are "outputs". A run whose
  // failed review triggered a corrected attempt is superseded — the corrected
  // run appears instead, so you never read the rejected version by accident.
  const outputs = $derived(
    runs.filter((r) => r.status === 'completed' && (r.result ?? '').trim() && !isSuperseded(r)),
  )
  // Starred-only narrows the pool; source chips + their counts then describe it.
  const base = $derived(starredOnly ? outputs.filter((r) => r.starred) : outputs)
  const shown = $derived(filter === 'all' ? base : base.filter((r) => r.source === filter))
  const countFor = (s: Source) =>
    s === 'all' ? base.length : base.filter((r) => r.source === s).length

  async function toggleStar(r: RunRecord, e: Event) {
    e.preventDefault()
    e.stopPropagation()
    const want = !r.starred
    runs = runs.map((x) => (x.id === r.id ? { ...x, starred: want } : x)) // optimistic
    try {
      await api.starOutput(r.id, want)
    } catch {
      runs = runs.map((x) => (x.id === r.id ? { ...x, starred: !want } : x)) // revert
      toast('could not update star', 'err')
    }
  }

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

  // A short report from a delta-mode runner is usually "nothing much changed" —
  // render it as a quiet, compact card rather than a full delivery.
  const isQuietDelta = (r: RunRecord) =>
    !!runnerFor(r)?.delta && (r.result ?? '').trim().length < 280

  // Files the run produced (the artifacts table, attached to list responses),
  // as {index, name} for download chips. Index keys the download endpoint.
  function artifactsOf(r: RunRecord): { index: number; name: string }[] {
    const raw = r.artifacts
    if (!Array.isArray(raw)) return []
    return raw.map((p, index) => ({ index, name: String(p).split(/[\\/]/).pop() ?? String(p) }))
  }

  async function copyResult(r: RunRecord) {
    try {
      await navigator.clipboard.writeText(r.result ?? '')
      toast('copied to clipboard', 'ok')
    } catch {
      toast('clipboard unavailable', 'err')
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
    error = null // clear a stale banner so a recovered search/refresh shows clean
    try {
      ;[runs, runners] = await Promise.all([
        api.runs({ limit: 200, exclude_chats: true, status: 'completed', q }),
        api.schedules(),
      ])
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
    // Reading your deliveries: when not searching, highlight what was unread and
    // mark it read on the server (durable + clears the sidebar badge). Search is
    // exploration, so it doesn't touch read state.
    if (!q.trim()) {
      const unread = outputs.filter((r) => !r.read_at).map((r) => r.id)
      if (unread.length) {
        freshIds = new Set(unread)
        try {
          await api.markOutputsRead(unread)
          bumpActivity() // nudge the sidebar badge to recount
        } catch {
          /* leave them unread — the badge just won't clear yet */
        }
      }
    }
  }
  // Debounced: initial load (q='') and every search keystroke run through here.
  $effect(() => {
    void q
    const t = setTimeout(() => void load(), 220)
    return () => clearTimeout(t)
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
    <Skeleton rows={5} />
  {:else}
    {#if outputs.length || q.trim() || starredOnly}
      <div class="out-controls">
        <input class="field out-search" type="search" bind:value={q}
               placeholder="search outputs…" aria-label="search outputs" />
        <div class="out-chips">
          {#each SOURCES as s}
            <button class="chip" class:on={filter === s} onclick={() => (filter = s)}>
              {s} <span class="chip-n">{countFor(s)}</span>
            </button>
          {/each}
          <button class="chip star-chip" class:on={starredOnly}
                  onclick={() => (starredOnly = !starredOnly)}
                  aria-pressed={starredOnly} title="show starred only">★ starred</button>
        </div>
      </div>
    {/if}

    {#if !shown.length}
      {#if q.trim()}
        <EmptyState title="no matches" hint={`nothing in your outputs matches “${q.trim()}”`} />
      {:else if starredOnly}
        <EmptyState title="no starred outputs" hint="star an output (☆) to keep it here" />
      {:else}
        <EmptyState title="no outputs yet" hint="when a runner fires or a background agent finishes, its result lands here to read">
          <a href={href('/runners')} use:link class="text-accent-soft" style="text-decoration: none;">create a runner →</a>
        </EmptyState>
      {/if}
    {:else}
    {#each groups as g}
      <div class="day-label label-cap">{g.label}</div>
      <div class="space-y-3" style="margin-bottom: 26px;">
        {#each g.items as r (r.id)}
          {@const runner = runnerFor(r)}
          {@const badge = verifyBadge(r)}
          <article class="card output" class:fresh={freshIds.has(r.id)} class:quiet={isQuietDelta(r)} class:alerted={isAlertResult(r)}>
            <header class="out-head">
              {#if freshIds.has(r.id)}<span class="new-dot" title="new since your last visit"></span>{/if}
              <a href={href('/runs/' + r.id)} use:link class="out-agent text-bright">{titleFor(r)}</a>
              <span class="src src-{r.source}">{r.source}</span>
              {#if isAlertResult(r)}<span class="alert-tag" title="this run crossed its alert bar">🚨 alert</span>{/if}
              {#if runner?.delta}<span class="delta-tag" title="delta report — only what changed since the last run">Δ</span>{/if}
              {#if badge}<span class="pill {badge.tone === 'ok' ? 'ok' : 'warn'}" style="font-size: 10px;" title="reviewed by the verification evaluator">{badge.label}</span>{/if}
              <span class="text-muted out-task" title={r.task}>{runner ? `${r.agent} · ${r.task}` : r.task}</span>
              <span class="ml-auto text-dim" style="font-size: 11px; white-space: nowrap;" title={localDateTime(r.started_at)}>{relativeTime(r.started_at)}</span>
              <button class="star-btn" class:on={r.starred} aria-pressed={r.starred}
                      title={r.starred ? 'unstar' : 'star'} aria-label={r.starred ? 'unstar output' : 'star output'}
                      onclick={(e) => toggleStar(r, e)}>{r.starred ? '★' : '☆'}</button>
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
              <button class="act" onclick={() => copyResult(r)}>copy</button>
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
  /* "Nothing much changed" delta reports: compact + visually quiet. */
  .output.quiet { padding: 10px 18px; background: transparent; }
  .output.quiet :global(.prose-md) { font-size: 14px; color: var(--fg-dim); }
  .delta-tag {
    display: inline-block;
    padding: 0 6px;
    border-radius: 4px;
    background: var(--accent-glow);
    color: var(--accent-soft);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: help;
  }
  /* A sensing runner that crossed its bar — the one card that must not be missed. */
  .output.alerted { border-color: color-mix(in srgb, var(--red) 55%, var(--border)); }
  .alert-tag {
    display: inline-block;
    padding: 0 6px;
    border-radius: 4px;
    background: color-mix(in srgb, var(--red) 14%, transparent);
    color: var(--red);
    font-family: var(--font-mono);
    font-size: 10px;
    white-space: nowrap;
    cursor: help;
  }
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

  /* Controls row: search on the left, source + starred chips on the right. */
  .out-controls {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 18px;
  }
  .out-search { max-width: 280px; }
  .out-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-left: auto; }
  .star-chip.on { color: var(--accent-soft); }

  /* Per-card star toggle (far right of the header). */
  .star-btn {
    background: transparent;
    border: 0;
    padding: 0 2px;
    margin-left: 2px;
    font-size: 15px;
    line-height: 1;
    color: var(--muted);
    cursor: pointer;
    align-self: center;
    transition: color 0.12s var(--transition), transform 0.1s var(--transition);
  }
  .star-btn:hover { color: var(--accent-soft); }
  .star-btn:active { transform: scale(0.88); }
  .star-btn.on { color: var(--accent-soft); }
</style>
