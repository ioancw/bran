<script lang="ts">
  // Outputs: a reading surface for what your agents actually produced —
  // the result *text* of completed runner fires, spawns, and one-shots, grouped
  // by day and rendered inline. This is the "delivery" half of the agent-runner:
  // Runs is the activity *log* (a debug table); Outputs is where you read the
  // briefing without digging into a transcript.
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { relativeTime, localDateTime } from '../lib/time'
  import { bumpActivity } from '../lib/workspace.svelte'
  import { isAlertResult, isSuperseded, verifyBadge } from '../lib/verification'
  import { toast } from '../lib/toast.svelte'
  import Page from '../components/Page.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import Prose from '../components/Prose.svelte'
  import Icon from '../components/Icon.svelte'
  import type { RunRecord, ScheduleRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let runners = $state<ScheduleRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  let expanded = $state<Record<string, boolean>>({})
  // Page size grows via "Show older outputs" — no silent 200-row cliff.
  let limit = $state(200)
  let loadingMore = $state(false)
  const maybeMore = $derived(runs.length >= limit)

  // Free-text search (server-side, over every completed output) + a starred-only
  // view. Read/star state is durable + cross-browser (server output_state).
  let q = $state('')
  let starredOnly = $state(false)

  // Which outputs were unread when this view loaded — kept so the cards that
  // were new at that moment stay highlighted for the visit, even though we mark
  // them read on the server straight away (which clears the sidebar badge).
  let freshIds = $state<Set<string>>(new Set())
  let copiedId = $state<string | null>(null)
  let copiedTimer: ReturnType<typeof setTimeout> | undefined

  // Outputs come from autonomous/managed runs, never interactive chat turns.
  const SOURCES = ['all', 'runner', 'spawn', 'manual'] as const
  type Source = (typeof SOURCES)[number]
  let filter = $state<Source>('all')
  const label = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

  // A runner-fired output is titled by the *runner* (the thing you named),
  // not the agent that powers it.
  const runnerFor = (r: RunRecord): ScheduleRecord | null =>
    r.schedule_id ? (runners.find((s) => s.id === r.schedule_id) ?? null) : null
  const titleFor = (r: RunRecord): string => runnerFor(r)?.name ?? r.agent

  // Only completed runs that actually produced text are "outputs". A run whose
  // failed review triggered a corrected attempt is superseded — the corrected
  // run appears instead, so you never read the rejected version by accident.
  // Subagent runs are a turn's internal delegation; their result is folded
  // into the parent's answer, so they aren't deliveries in their own right.
  const outputs = $derived(
    runs.filter(
      (r) =>
        r.status === 'completed' && (r.result ?? '').trim() && !isSuperseded(r) && r.source !== 'subagent',
    ),
  )
  // Starred-only narrows the pool; source chips + their counts then describe it.
  const base = $derived(starredOnly ? outputs.filter((r) => r.starred) : outputs)
  const shown = $derived(filter === 'all' ? base : base.filter((r) => r.source === filter))
  const countFor = (s: Source) => (s === 'all' ? base.length : base.filter((r) => r.source === s).length)

  async function toggleStar(r: RunRecord, e: Event) {
    e.preventDefault()
    e.stopPropagation()
    const want = !r.starred
    runs = runs.map((x) => (x.id === r.id ? { ...x, starred: want } : x)) // optimistic
    try {
      await api.starOutput(r.id, want)
    } catch {
      runs = runs.map((x) => (x.id === r.id ? { ...x, starred: !want } : x)) // revert
      toast('Could not update star', 'err')
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
    return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
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
  const isQuietDelta = (r: RunRecord) => !!runnerFor(r)?.delta && (r.result ?? '').trim().length < 280

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
      copiedId = r.id
      clearTimeout(copiedTimer)
      copiedTimer = setTimeout(() => (copiedId = null), 2000)
    } catch {
      toast('Clipboard unavailable', 'err')
    }
  }

  // "Discuss" hands the output to the orchestrator: open a new chat with the
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
        api.runs({ limit, exclude_chats: true, status: 'completed', q }),
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
  {#snippet subtitle()}What your agents produced{/snippet}

  {#if error}<ErrorBanner {error} onretry={load} />{/if}
  {#if !loaded}
    <Skeleton rows={5} />
  {:else}
    {#if outputs.length || q.trim() || starredOnly}
      <div class="out-controls">
        <div class="search-wrap">
          <Icon name="search" size={14} class="search-ico" />
          <input
            class="field out-search"
            type="search"
            bind:value={q}
            placeholder="Search outputs"
            aria-label="Search outputs"
          />
        </div>
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
          <button
            class="seg-btn"
            class:on={starredOnly}
            onclick={() => (starredOnly = !starredOnly)}
            aria-pressed={starredOnly}
            title="Show starred only"><Icon name="star" size={12} />Starred</button
          >
        </div>
      </div>
    {/if}

    {#if !shown.length}
      {#if q.trim()}
        <EmptyState
          title="No matches"
          hint={`Nothing in your outputs matches “${q.trim()}”.`}
          icon="search"
        />
      {:else if starredOnly}
        <EmptyState title="No starred outputs" hint="Star an output to keep it here." icon="star" />
      {:else}
        <EmptyState
          title="No outputs yet"
          hint="When a runner fires or a background agent finishes, its result lands here to read."
          icon="inbox"
        >
          <a href={href('/runners')} use:link class="btn-primary">
            <Icon name="plus" size={14} stroke={2.2} />Create a runner
          </a>
        </EmptyState>
      {/if}
    {:else}
      {#each groups as g (g.label)}
        <div class="day-label label-cap">{g.label}</div>
        <div class="space-y-3" style="margin-bottom: 26px;">
          {#each g.items as r (r.id)}
            {@const runner = runnerFor(r)}
            {@const badge = verifyBadge(r)}
            <article
              class="card output"
              class:fresh={freshIds.has(r.id)}
              class:quiet={isQuietDelta(r)}
              class:alerted={isAlertResult(r)}
            >
              <header class="out-head">
                {#if freshIds.has(r.id)}<span class="dot accent" title="New since your last visit"
                  ></span>{/if}
                <a href={href('/runs/' + r.id)} use:link class="out-agent">{titleFor(r)}</a>
                <span class="src src-{r.source}">{r.source}</span>
                {#if isAlertResult(r)}<span class="pill err" title="This run crossed its alert bar"
                    ><Icon name="alert-triangle" size={12} />Alert</span
                  >{/if}
                {#if runner?.delta}<span
                    class="pill accent"
                    title="Delta report: only what changed since the last run">Delta</span
                  >{/if}
                {#if badge}<span
                    class="pill {badge.tone === 'ok' ? 'ok' : 'warn'}"
                    title="Reviewed by the verification evaluator">{badge.label}</span
                  >{/if}
                <span class="text-muted out-task" title={r.task}
                  >{runner ? `${r.agent} · ${r.task}` : r.task}</span
                >
                <span class="out-time" title={localDateTime(r.started_at)}>{relativeTime(r.started_at)}</span>
                <button
                  class="btn-icon star-btn"
                  class:on={r.starred}
                  aria-pressed={r.starred}
                  title={r.starred ? 'Unstar' : 'Star'}
                  aria-label={r.starred ? 'Unstar output' : 'Star output'}
                  onclick={(e) => toggleStar(r, e)}><Icon name="star" size={15} /></button
                >
              </header>
              <div class="out-body reading-col" class:clamp={isLong(r.result ?? '') && !expanded[r.id]}>
                <Prose text={r.result ?? ''} />
              </div>
              {#if isLong(r.result ?? '')}
                <button
                  class="btn-text more"
                  aria-expanded={!!expanded[r.id]}
                  onclick={() => (expanded = { ...expanded, [r.id]: !expanded[r.id] })}
                >
                  {expanded[r.id] ? 'Show less' : 'Show more'}
                </button>
              {/if}
              <footer class="out-actions">
                {#each artifactsOf(r) as a (a.index)}
                  <a
                    href={'/spa/runs/' + encodeURIComponent(r.id) + '/artifacts/' + a.index}
                    download
                    class="btn-outline btn-sm art-chip"
                    title="Download file produced by this run"
                    ><Icon name="file-text" size={12} /><span class="mono">{a.name}</span></a
                  >
                {/each}
                <span class="spacer"></span>
                <button class="btn-ghost btn-sm" onclick={() => copyResult(r)}>
                  {#if copiedId === r.id}<Icon name="check" size={13} />Copied{:else}<Icon
                      name="copy"
                      size={13}
                    />Copy{/if}
                </button>
                <button class="btn-ghost btn-sm" onclick={() => discuss(r)}
                  ><Icon name="message-square" size={13} />Discuss</button
                >
                {#if runner}
                  <a
                    href={href('/runners/' + encodeURIComponent(runner.name))}
                    use:link
                    class="btn-ghost btn-sm"
                  >
                    <Icon name="bot" size={13} />Open runner
                  </a>
                {/if}
              </footer>
            </article>
          {/each}
        </div>
      {/each}
      {#if maybeMore}
        <div style="text-align: center; margin: 4px 0 20px;">
          <button class="btn-outline" disabled={loadingMore} onclick={loadMore}>
            {loadingMore ? 'Loading…' : 'Show older outputs'}
          </button>
        </div>
      {/if}
    {/if}
  {/if}
</Page>

<style>
  .day-label {
    display: block;
    margin: 0 0 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }
  .output {
    padding: 16px 18px;
  }
  .output.fresh {
    border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
  }
  /* "Nothing much changed" delta reports: compact + visually quiet. */
  .output.quiet {
    padding: 10px 18px;
    background: transparent;
  }
  .output.quiet .reading-col :global(.prose-md) {
    font-family: var(--font-ui);
    font-size: 13.5px;
    color: var(--fg-dim);
  }
  /* A sensing runner that crossed its bar — the one card that must not be missed. */
  .output.alerted {
    border-color: color-mix(in srgb, var(--red) 55%, var(--border));
  }
  .out-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .out-agent {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .out-task {
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 46ch;
  }
  .out-time {
    margin-left: auto;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
  .star-btn.on {
    color: var(--amber);
  }
  .star-btn.on :global(svg) {
    fill: currentColor;
  }

  /* The briefing is the product: it reads like print. */
  .reading-col {
    max-width: var(--chat-col);
  }
  .reading-col :global(.prose-md) {
    font-family: var(--font-prose);
    font-size: 16.5px;
    line-height: 1.7;
  }
  /* Collapsed long outputs fade out; "Show more" expands them in place. */
  .out-body {
    position: relative;
  }
  .out-body.clamp {
    max-height: 220px;
    overflow: hidden;
    -webkit-mask-image: linear-gradient(to bottom, #000 70%, transparent);
    mask-image: linear-gradient(to bottom, #000 70%, transparent);
  }
  .more {
    margin-top: 8px;
  }

  .out-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
    flex-wrap: wrap;
  }
  .out-actions .spacer {
    flex: 1;
  }
  .art-chip {
    text-decoration: none;
  }
  .art-chip .mono {
    font-size: 11.5px;
  }

  /* Controls row: search on the left, source + starred segments on the right. */
  .out-controls {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 18px;
  }
  .search-wrap {
    position: relative;
    width: min(300px, 100%);
  }
  .search-wrap :global(.search-ico) {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--muted);
    pointer-events: none;
  }
  .search-wrap input.out-search.field {
    padding-left: 32px;
  }
  .seg {
    display: inline-flex;
    gap: 4px;
    flex-wrap: wrap;
    margin-left: auto;
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
  .seg-n {
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .seg-btn.on .seg-n {
    color: inherit;
    opacity: 0.75;
  }
</style>
