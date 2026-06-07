<script lang="ts">
  // Runner detail: a managed agent's trigger + task, a "run now" control, and
  // its run history. (Runs link to agent/project, not to a specific runner yet,
  // so history is the agent's scheduled/manual runs.)
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { fmtCost, relativeTime, localDateTime } from '../lib/time'
  import { errorText } from '../lib/errors'
  import { confirmDialog } from '../lib/confirm.svelte'
  import Page from '../components/Page.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import type { ProjectSummary, RunRecord, ScheduleRecord } from '../lib/types'

  let { runnerName }: { runnerName: string } = $props()

  let runner = $state<ScheduleRecord | null>(null)
  let runs = $state<RunRecord[]>([])
  let projects = $state<ProjectSummary[]>([])
  let error = $state<string | null>(null)
  let loaded = $state(false)
  let firing = $state(false)
  let busy = $state(false)

  const projName = (id: string | null) => (id ? projects.find((p) => p.id === id)?.name ?? id : null)

  async function load() {
    try {
      const [schedules, ps] = await Promise.all([api.schedules(), api.projects()])
      runner = schedules.find((s) => s.name === runnerName) ?? null
      projects = ps
      if (runner) runs = await api.runs({ schedule_id: runner.id, limit: 50 })
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void runnerName
    void load()
  })

  async function runNow() {
    if (!runner || firing) return
    firing = true
    try {
      const r = await api.newRun(runner.agent, runner.task || `Run ${runner.agent}`, { schedule_id: runner.id })
      navigate('/runs/' + r.id)
    } catch (e) {
      error = String(e)
      firing = false
    }
  }
  async function toggleEnabled() {
    if (!runner || busy) return
    busy = true
    try {
      runner = await api.setScheduleEnabled(runner.name, !runner.enabled)
    } catch (e) {
      error = String(e)
    } finally {
      busy = false
    }
  }
  async function remove() {
    if (!runner) return
    if (!(await confirmDialog(`Delete runner "${runner.name}"?`))) return
    await api.deleteSchedule(runner.name)
    navigate('/runners')
  }
</script>

<Page title={runnerName}>
  {#snippet subtitle()}runner{/snippet}
  {#snippet actions()}
    {#if runner}<button class="btn-outline" onclick={remove}>delete</button>{/if}
  {/snippet}

  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {:else if !runner}
    <div class="empty-state"><h3>unknown runner</h3></div>
  {:else}
    <div class="grid grid-cols-3 gap-6">
      <!-- Main: run now + run history -->
      <div class="col-span-2 space-y-6">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Task</div>
          <div class="text-fg" style="font-size: 14px; white-space: pre-wrap; margin-bottom: 12px;">{runner.task || '—'}</div>
          <div style="display: flex; justify-content: flex-end;">
            <button class="btn-primary" disabled={firing} onclick={runNow}>{firing ? 'starting…' : 'run now →'}</button>
          </div>
        </div>

        <section>
          <div class="label-cap" style="margin-bottom: 8px;">Runs ({runs.length})</div>
          {#if runs.length}
            <div class="space-y-2">
              {#each runs.slice(0, 20) as r}
                <a href={href('/runs/' + r.id)} use:link class="row-link card-quiet" style="display: flex; gap: 10px; align-items: baseline; text-decoration: none;">
                  <span class="src src-{r.source}">{r.source}</span>
                  <StatusBadge status={r.status} />
                  <span class="text-dim" style="font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.task}</span>
                  <span class="ml-auto text-muted" style="font-size: 11px; white-space: nowrap;">{relativeTime(r.started_at)} · {fmtCost(r.total_cost_usd)}</span>
                </a>
              {/each}
            </div>
          {:else}
            <div class="text-muted" style="font-size: 13px; font-style: italic;">no runs yet — fire one above.</div>
          {/if}
        </section>
      </div>

      <!-- Right: trigger + config -->
      <aside class="col-span-1 space-y-4">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Trigger</div>
          <div style="font-size: 13px;" class="space-y-2">
            {#if runner.run_at}
              <div><span class="label-cap">once</span><br /><span class="mono text-bright">{localDateTime(runner.run_at)}</span></div>
            {:else}
              <div><span class="label-cap">schedule</span><br /><span class="mono text-bright">{runner.cron}</span></div>
            {/if}
            <div><span class="label-cap">next</span> <span class="text-dim">{runner.enabled && runner.next_run ? localDateTime(runner.next_run) : '—'}</span></div>
            <div style="display: flex; align-items: center; gap: 10px;">
              <span class="src" style="color: {runner.enabled ? 'var(--accent-soft)' : 'var(--muted)'};">{runner.enabled ? 'enabled' : 'paused'}</span>
              <button class="btn-ghost" disabled={busy} onclick={toggleEnabled}>{busy ? '…' : runner.enabled ? 'pause' : 'resume'}</button>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Config</div>
          <div style="font-size: 13px;" class="space-y-2">
            <div>
              <span class="label-cap">agent</span>
              <a href={href('/agents/' + encodeURIComponent(runner.agent))} use:link class="text-accent-soft" style="text-decoration: none;">{runner.agent} →</a>
            </div>
            <div>
              <span class="label-cap">project</span>
              {#if runner.project_id}
                <a href={href('/projects/' + runner.project_id)} use:link class="text-accent-soft" style="text-decoration: none;">{projName(runner.project_id)} →</a>
              {:else}
                <span class="text-muted">standalone</span>
              {/if}
            </div>
          </div>
        </div>
      </aside>
    </div>
  {/if}
</Page>

<style>
  .row-link {
    padding: 7px 10px;
    border-radius: var(--radius);
    transition: background 0.12s var(--transition);
  }
  .row-link:hover { background: var(--surface2); }
  /* .src lives in global.css (shared source-pill component). */
</style>
