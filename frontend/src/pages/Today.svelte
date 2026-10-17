<script lang="ts">
  // Today: the assistant's home surface — what happened and what's coming.
  // Stitches the fleet together at a glance: failures needing attention,
  // today's deliveries (with unread markers), upcoming runner fires with live
  // countdowns, and a one-line activity tally. Composes the existing
  // /spa/runs + /spa/schedules data; no dedicated backend endpoint.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { relativeTime, localDateTime, localClock, countdown, fmtCost } from '../lib/time'
  import { isAlertResult, isSuperseded, verifyBadge } from '../lib/verification'
  import Page from '../components/Page.svelte'
  import Icon from '../components/Icon.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import type { RunRecord, ScheduleRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let schedules = $state<ScheduleRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)

  // A slow tick keeps the countdowns honest while the page sits open.
  let now = $state(Date.now())
  $effect(() => {
    const t = setInterval(() => (now = Date.now()), 30_000)
    return () => clearInterval(t)
  })

  const isToday = (iso: string) => {
    const d = new Date(iso)
    const n = new Date(now)
    return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
  }

  const runnerFor = (r: RunRecord): ScheduleRecord | null =>
    r.schedule_id ? (schedules.find((s) => s.id === r.schedule_id) ?? null) : null
  const titleFor = (r: RunRecord): string => runnerFor(r)?.name ?? r.agent

  // --- Sections -------------------------------------------------------------
  // Superseded = a failed-review run whose corrected attempt is also in the
  // list; show only the version worth reading.
  const outputs = $derived(
    runs.filter(
      (r) =>
        r.status === 'completed' && (r.result ?? '').trim() && !isSuperseded(r) && r.source !== 'subagent',
    ),
  )
  // Deliveries: today's outputs; quiet mornings fall back to the latest few so
  // the page never opens onto a void.
  const todays = $derived(outputs.filter((r) => isToday(r.started_at)))
  const deliveries = $derived(todays.length ? todays : outputs.slice(0, 3))
  const deliveriesLabel = $derived(todays.length ? "Today's deliveries" : 'Latest deliveries')

  // Failures from the last 48h are "needs attention" — older ones are history.
  const attention = $derived(
    runs
      .filter((r) => r.status === 'failed' && now - new Date(r.started_at).getTime() < 48 * 3600_000)
      .slice(0, 5),
  )

  const upcoming = $derived(
    schedules
      .filter((s) => s.enabled && s.next_run)
      .sort((a, b) => (a.next_run! < b.next_run! ? -1 : 1))
      .slice(0, 5),
  )

  const stats = $derived.by(() => {
    const t = runs.filter((r) => isToday(r.started_at))
    return {
      completed: t.filter((r) => r.status === 'completed').length,
      failed: t.filter((r) => r.status === 'failed').length,
      running: t.filter((r) => r.status === 'running' || r.status === 'pending').length,
      cost: t.reduce((sum, r) => sum + (r.total_cost_usd ?? 0), 0),
    }
  })

  // First lines of real prose for the delivery cards — skip markdown chrome
  // (headers, lists, tables) and strip inline markup so the snippet reads
  // like a sentence, not raw source.
  function snippet(text: string, limit = 220): string {
    const parts: string[] = []
    for (const raw of text.split('\n')) {
      const line = raw.trim()
      if (!line || /^[#\-*|>`]/.test(line)) continue
      parts.push(line)
      if (parts.join(' ').length > limit + 20) break
    }
    let s = parts
      .join(' ')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/\*([^*]+)\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/__([^_]+)__/g, '$1')
    if (s.length > limit) s = s.slice(0, limit - 1).replace(/\s+\S*$/, '') + '…'
    return s || text.slice(0, limit)
  }

  const dateLabel = $derived(
    new Date(now).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }),
  )

  async function load() {
    error = null // clear a stale banner so a recovered refresh shows clean
    try {
      ;[runs, schedules] = await Promise.all([api.runs({ limit: 200, exclude_chats: true }), api.schedules()])
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void load()
  })
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

<Page title="Today">
  {#snippet subtitle()}{dateLabel}{/snippet}

  {#if error}<ErrorBanner {error} onretry={load} />{/if}
  {#if !loaded}
    <Skeleton rows={5} />
  {:else if !runs.length && !schedules.length}
    <EmptyState
      title="Nothing here yet"
      hint="Once your agents start working, this page becomes their morning report: deliveries, failures, and what's coming up."
      icon="calendar"
    >
      <a href={href('/runners')} use:link class="btn-primary"><Icon name="plus" size={14} />Create a runner</a
      >
      <a href={href('/chat')} use:link class="btn-outline">Start a chat</a>
    </EmptyState>
  {:else}
    <div class="grid grid-cols-3 gap-6">
      <!-- Main column: attention + deliveries -->
      <div class="col-span-2 space-y-6">
        {#if attention.length}
          <section>
            <div class="sec-head">
              <span class="label-cap sec-alert"><Icon name="alert-circle" size={13} />Needs attention</span>
            </div>
            <div class="space-y-2">
              {#each attention as r (r.id)}
                <a href={href('/runs/' + r.id)} use:link class="att card">
                  <span class="att-title">{titleFor(r)}</span>
                  <span class="src src-{r.source}">{r.source}</span>
                  <span class="ml-auto meta">{relativeTime(r.started_at)}</span>
                  <span class="att-err" title={r.error ?? ''}
                    >{r.error ?? 'Failed with no error message'}</span
                  >
                </a>
              {/each}
            </div>
          </section>
        {/if}

        <section>
          <div class="sec-head">
            <span class="label-cap">{deliveriesLabel}</span>
            <a href={href('/outputs')} use:link class="ml-auto sec-more"
              >All outputs<Icon name="arrow-right" size={13} /></a
            >
          </div>
          {#if deliveries.length}
            <div class="space-y-2">
              {#each deliveries as r (r.id)}
                {@const badge = verifyBadge(r)}
                <a href={href('/runs/' + r.id)} use:link class="dlv card">
                  <header class="dlv-head">
                    {#if !r.read_at}<span class="dot accent" title="Unread"></span>{/if}
                    <span class="dlv-title">{titleFor(r)}</span>
                    <span class="src src-{r.source}">{r.source}</span>
                    {#if isAlertResult(r)}<span class="pill err" title="This run crossed its alert bar"
                        ><Icon name="alert-triangle" size={12} />Alert</span
                      >{/if}
                    {#if runnerFor(r)?.delta}<span
                        class="pill accent"
                        title="Delta report: only what changed since the last run">Delta</span
                      >{/if}
                    {#if badge}<span
                        class="pill {badge.tone === 'ok' ? 'ok' : 'warn'}"
                        title="Reviewed by the verification evaluator">{badge.label}</span
                      >{/if}
                    <span class="ml-auto meta" title={localDateTime(r.started_at)}
                      >{relativeTime(r.started_at)}</span
                    >
                  </header>
                  <p class="dlv-snippet">{snippet(r.result ?? '')}</p>
                </a>
              {/each}
            </div>
          {:else}
            <p class="quiet">
              Nothing delivered yet. <a href={href('/runners')} use:link>Create a runner</a> to change that.
            </p>
          {/if}
        </section>
      </div>

      <!-- Right rail: coming up + today's tally -->
      <aside class="col-span-1 space-y-4">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Coming up</div>
          {#if upcoming.length}
            <div class="up-list">
              {#each upcoming as s (s.id)}
                <a href={href('/runners/' + encodeURIComponent(s.name))} use:link class="up-row">
                  <span class="up-name">{s.name}</span>
                  <span class="up-when">{countdown(s.next_run!, now)}</span>
                  <span class="up-meta">{s.agent} · {localClock(s.next_run!)}</span>
                </a>
              {/each}
            </div>
          {:else}
            <p class="quiet">No scheduled runners.</p>
          {/if}
        </div>

        <div class="card-quiet">
          <div class="label-cap" style="margin-bottom: 10px;">Today's activity</div>
          <div class="tally">
            <div class="stat">
              <span class="stat-num num">{stats.completed}</span>
              <span class="stat-label">Done</span>
            </div>
            <div class="stat">
              <span class="stat-num num" class:bad={stats.failed > 0}>{stats.failed}</span>
              <span class="stat-label">Failed</span>
            </div>
            <div class="stat">
              <span class="stat-num num">{stats.running}</span>
              <span class="stat-label">Running</span>
            </div>
            <div class="stat">
              <span class="stat-num num">{fmtCost(stats.cost)}</span>
              <span class="stat-label">Cost</span>
            </div>
          </div>
          <a href={href('/runs')} use:link class="sec-more" style="margin-top: 12px;"
            >Activity log<Icon name="arrow-right" size={13} /></a
          >
        </div>
      </aside>
    </div>
  {/if}
</Page>

<style>
  .sec-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
  }
  .sec-alert {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--red);
  }
  .sec-more {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
    text-decoration: none;
  }
  .sec-more:hover {
    color: var(--accent-soft);
    text-decoration: none;
  }
  .meta {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
  .quiet {
    font-size: 13px;
    color: var(--muted);
  }

  .att {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding: 10px 14px;
    text-decoration: none;
    border-color: color-mix(in srgb, var(--red) 30%, var(--border));
  }
  .att:hover {
    text-decoration: none;
  }
  .att-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
  }
  .att-err {
    flex-basis: 100%;
    color: var(--red);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .dlv {
    display: block;
    padding: 14px 16px;
    text-decoration: none;
  }
  .dlv:hover {
    text-decoration: none;
  }
  .dlv-head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 6px;
  }
  .dlv-title {
    font-size: 14px;
    font-weight: 500;
    color: var(--fg-bright);
  }
  .dlv-snippet {
    margin: 0;
    font-size: 13px;
    line-height: 1.55;
    color: var(--fg-dim);
  }

  .up-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .up-row {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 2px 8px;
    text-decoration: none;
    padding: 6px 8px;
    margin: 0 -8px;
    border-radius: var(--radius);
    transition: background var(--dur-1) var(--transition);
  }
  .up-row:hover {
    background: var(--surface2);
    text-decoration: none;
  }
  .up-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .up-when {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .up-meta {
    grid-column: 1 / -1;
    font-size: 12px;
    color: var(--muted);
  }

  .tally {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px 16px;
  }
  .stat {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .stat-num {
    font-size: 20px;
    font-weight: 600;
    line-height: 1.2;
    color: var(--fg-bright);
    letter-spacing: -0.01em;
  }
  .stat-num.bad {
    color: var(--red);
  }
  .stat-label {
    font-size: 12px;
    color: var(--muted);
  }
</style>
