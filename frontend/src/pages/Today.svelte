<script lang="ts">
  // Today: the assistant's home surface — what happened and what's coming.
  // Stitches the fleet together at a glance: failures needing attention,
  // today's deliveries (with unread markers), upcoming runner fires with live
  // countdowns, and a one-line activity tally. Composes the existing
  // /spa/runs + /spa/schedules data; no dedicated backend endpoint.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { relativeTime, localDateTime, localClock, countdown, fmtCost } from '../lib/time'
  import { errorText } from '../lib/errors'
  import { outputsSeen, isNewSince } from '../lib/seen.svelte'
  import { isSuperseded, verifyBadge } from '../lib/verification'
  import Page from '../components/Page.svelte'
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
    runs.filter((r) => r.status === 'completed' && (r.result ?? '').trim() && !isSuperseded(r)),
  )
  // Deliveries: today's outputs; quiet mornings fall back to the latest few so
  // the page never opens onto a void.
  const todays = $derived(outputs.filter((r) => isToday(r.started_at)))
  const deliveries = $derived(todays.length ? todays : outputs.slice(0, 3))
  const deliveriesLabel = $derived(todays.length ? "Today's deliveries" : 'Latest deliveries')

  // Failures from the last 48h are "needs attention" — older ones are history.
  const attention = $derived(
    runs.filter((r) => r.status === 'failed' && now - new Date(r.started_at).getTime() < 48 * 3600_000).slice(0, 5),
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
  // (headers, lists, tables) so the snippet reads like a sentence.
  function snippet(text: string, limit = 220): string {
    const parts: string[] = []
    for (const raw of text.split('\n')) {
      const line = raw.trim()
      if (!line || /^[#\-*|>`]/.test(line)) continue
      parts.push(line)
      if (parts.join(' ').length > limit + 20) break
    }
    let s = parts.join(' ')
    if (s.length > limit) s = s.slice(0, limit - 1).replace(/\s+\S*$/, '') + '…'
    return s || text.slice(0, limit)
  }

  const dateLabel = $derived(
    new Date(now).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }),
  )

  async function load() {
    try {
      ;[runs, schedules] = await Promise.all([
        api.runs({ limit: 200, exclude_chats: true }),
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

  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <Skeleton rows={5} />
  {:else if !runs.length && !schedules.length}
    <EmptyState title="nothing yet" hint="once your agents start working, this page becomes their morning report">
      <a href={href('/runners')} use:link class="text-accent-soft" style="text-decoration: none;">create a runner →</a>
      <span class="text-muted" style="margin: 0 8px;">·</span>
      <a href={href('/chat')} use:link class="text-accent-soft" style="text-decoration: none;">start a chat →</a>
    </EmptyState>
  {:else}
    <div class="grid grid-cols-3 gap-6">
      <!-- Main column: attention + deliveries -->
      <div class="col-span-2 space-y-6">
        {#if attention.length}
          <section>
            <div class="sec-label label-cap" style="color: var(--red);">needs attention</div>
            <div class="space-y-2">
              {#each attention as r (r.id)}
                <a href={href('/runs/' + r.id)} use:link class="att card-quiet">
                  <span class="text-bright" style="font-size: 13px; font-weight: 500;">{titleFor(r)}</span>
                  <span class="src src-{r.source}">{r.source}</span>
                  <span class="ml-auto text-dim" style="font-size: 11px; white-space: nowrap;">{relativeTime(r.started_at)}</span>
                  <span class="att-err" title={r.error ?? ''}>{r.error ?? 'failed with no error message'}</span>
                </a>
              {/each}
            </div>
          </section>
        {/if}

        <section>
          <div class="sec-label label-cap" style="display: flex; align-items: baseline;">
            {deliveriesLabel}
            <a href={href('/outputs')} use:link class="ml-auto sec-more">all outputs →</a>
          </div>
          {#if deliveries.length}
            <div class="space-y-2">
              {#each deliveries as r (r.id)}
                {@const badge = verifyBadge(r)}
                <a href={href('/runs/' + r.id)} use:link class="dlv card">
                  <header style="display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px;">
                    {#if isNewSince(r.started_at, outputsSeen.at)}<span class="new-dot" title="unread"></span>{/if}
                    <span class="text-bright" style="font-size: 14px; font-weight: 500;">{titleFor(r)}</span>
                    <span class="src src-{r.source}">{r.source}</span>
                    {#if runnerFor(r)?.delta}<span class="delta-tag" title="delta report — only what changed since the last run">Δ</span>{/if}
                    {#if badge}<span class="pill {badge.tone === 'ok' ? 'ok' : 'warn'}" style="font-size: 10px;" title="reviewed by the verification evaluator">{badge.label}</span>{/if}
                    <span class="ml-auto text-dim" style="font-size: 11px; white-space: nowrap;" title={localDateTime(r.started_at)}>{relativeTime(r.started_at)}</span>
                  </header>
                  <p class="dlv-snippet">{snippet(r.result ?? '')}</p>
                </a>
              {/each}
            </div>
          {:else}
            <div class="text-muted" style="font-size: 13px; font-style: italic;">
              nothing delivered yet — <a href={href('/runners')} use:link class="text-accent-soft" style="text-decoration: none;">create a runner</a> to change that.
            </div>
          {/if}
        </section>
      </div>

      <!-- Right rail: coming up + today's tally -->
      <aside class="col-span-1 space-y-4">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 10px;">coming up</div>
          {#if upcoming.length}
            <div class="space-y-2">
              {#each upcoming as s (s.id)}
                <a href={href('/runners/' + encodeURIComponent(s.name))} use:link class="up-row">
                  <span class="up-name text-bright">{s.name}</span>
                  <span class="up-when mono text-accent-soft">{countdown(s.next_run!, now)}</span>
                  <span class="up-meta text-muted">{s.agent} · {localClock(s.next_run!)}</span>
                </a>
              {/each}
            </div>
          {:else}
            <div class="text-muted" style="font-size: 12.5px; font-style: italic;">no scheduled runners.</div>
          {/if}
        </div>

        <div class="card-quiet">
          <div class="label-cap" style="margin-bottom: 8px;">today's activity</div>
          <div class="tally">
            <div><span class="num text-bright">{stats.completed}</span> <span class="label-cap">done</span></div>
            <div><span class="num" style="color: {stats.failed ? 'var(--red)' : 'var(--fg-bright)'};">{stats.failed}</span> <span class="label-cap">failed</span></div>
            <div><span class="num text-bright">{stats.running}</span> <span class="label-cap">running</span></div>
            <div><span class="num text-bright">{fmtCost(stats.cost)}</span> <span class="label-cap">cost</span></div>
          </div>
          <a href={href('/runs')} use:link class="sec-more" style="display: inline-block; margin-top: 8px;">activity log →</a>
        </div>
      </aside>
    </div>
  {/if}
</Page>

<style>
  .sec-label {
    color: var(--muted);
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }
  .sec-more {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--muted);
    text-decoration: none;
    text-transform: none;
    letter-spacing: 0;
  }
  .sec-more:hover { color: var(--accent-soft); }

  .att {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
    padding: 10px 14px;
    text-decoration: none;
    border-left: 2px solid var(--red);
    border-radius: var(--radius);
    transition: background 0.12s var(--transition);
  }
  .att:hover { background: var(--surface2); }
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
    transition: border-color 0.12s var(--transition);
  }
  .dlv:hover { border-color: var(--muted); }
  .dlv-snippet {
    margin: 0;
    font-size: 13px;
    line-height: 1.55;
    color: var(--fg-dim);
  }
  .new-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--accent-soft);
    flex-shrink: 0;
    align-self: center;
  }
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

  .up-row {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 2px 8px;
    text-decoration: none;
    padding: 6px 8px;
    margin: 0 -8px;
    border-radius: var(--radius);
    transition: background 0.12s var(--transition);
  }
  .up-row:hover { background: var(--surface2); }
  .up-name {
    font-size: 13px;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .up-when { font-size: 11px; white-space: nowrap; }
  .up-meta {
    grid-column: 1 / -1;
    font-size: 11px;
    font-family: var(--font-mono);
  }

  .tally {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 12px;
    font-size: 13px;
  }
  .tally .num { font-family: var(--font-mono); font-size: 14px; margin-right: 4px; }
</style>
