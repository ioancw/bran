<script lang="ts">
  // Runners = standalone managed agents (scheduled, headless). Not owned by a
  // project; optionally *attached* to one to borrow its context/visibility.
  import { api } from '../lib/api'
  import { router, href, link } from '../lib/router.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { errorText } from '../lib/errors'
  import { toast } from '../lib/toast.svelte'
  import { localDateTime } from '../lib/time'
  import EmptyState from '../components/EmptyState.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import CronField from '../components/CronField.svelte'
  import type { AgentInfo, ProjectSummary, RunRecord, ScheduleRecord } from '../lib/types'

  let runners = $state<ScheduleRecord[]>([])
  let agents = $state<AgentInfo[]>([])
  let projects = $state<ProjectSummary[]>([])
  let recentRuns = $state<RunRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)

  // Each runner's most recent run (runs come newest-first), to flag failures
  // at a glance instead of burying them in the activity log.
  const lastRun = $derived.by(() => {
    const m: Record<string, RunRecord> = {}
    for (const r of recentRuns) {
      if (r.schedule_id && !(r.schedule_id in m)) m[r.schedule_id] = r
    }
    return m
  })

  let showForm = $state(false)
  let fName = $state('')
  let fAgent = $state('orchestrator')
  let fKind = $state<'cron' | 'once'>('cron')
  let fCron = $state('0 8 * * *')
  let fRunAt = $state('') // datetime-local value for one-shot runners
  let fTask = $state('')
  let fProject = $state('') // '' = standalone
  let fVerify = $state(false) // evaluator reviews each output, re-runs once on failure
  let fDelta = $state(false) // each run sees the previous report, reports only changes

  async function load() {
    error = null // clear a stale banner so a recovered refresh shows clean
    try {
      ;[runners, agents, projects, recentRuns] = await Promise.all([
        api.schedules(), api.agents(), api.projects(),
        api.runs({ limit: 200, exclude_chats: true }),
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

  // Prefill the agent when arriving from the Agents page (/runners?agent=research).
  $effect(() => {
    const a = router.route.query.get('agent')
    if (a) {
      fAgent = a
      showForm = true
    }
  })

  const projName = (id: string | null) =>
    id ? (projects.find((p) => p.id === id)?.name ?? id) : null

  async function create() {
    if (!fName.trim()) return
    const fields: { name: string; agent: string; task: string; project_id?: string; cron?: string; run_at?: string; verify?: boolean; delta?: boolean } = {
      name: fName.trim(), agent: fAgent, task: fTask, project_id: fProject || undefined,
      verify: fVerify, delta: fDelta,
    }
    if (fKind === 'once') {
      if (!fRunAt) return
      fields.run_at = new Date(fRunAt).toISOString() // local picker → UTC
    } else {
      if (!fCron.trim()) return
      fields.cron = fCron.trim()
    }
    try {
      await api.newSchedule(fields)
      toast(`created runner ${fields.name}`, 'ok')
    } catch (e) {
      error = String(e)
      return
    }
    fName = ''
    fTask = ''
    fProject = ''
    fRunAt = ''
    fVerify = false
    fDelta = false
    showForm = false
    await load()
  }
  async function remove(name: string) {
    if (!(await confirmDialog(`Delete runner "${name}"?`))) return
    try {
      await api.deleteSchedule(name)
      toast(`deleted runner ${name}`, 'ok')
    } catch (e) {
      toast(errorText(e), 'err')
    }
    await load()
  }
  async function toggle(r: ScheduleRecord) {
    // Optimistic: flip immediately, reconcile with the server's record, revert
    // on error — the button should never feel like it's waiting on a network.
    const want = !r.enabled
    runners = runners.map((x) => (x.name === r.name ? { ...x, enabled: want } : x))
    try {
      const updated = await api.setScheduleEnabled(r.name, want)
      runners = runners.map((x) => (x.name === r.name ? updated : x))
      toast(`${updated.enabled ? 'resumed' : 'paused'} ${r.name}`, 'ok')
    } catch (e) {
      runners = runners.map((x) => (x.name === r.name ? { ...x, enabled: r.enabled } : x))
      toast(errorText(e), 'err')
    }
  }
</script>

<Page title="Runners">
  {#snippet subtitle()}scheduled agents · {runners.length}{/snippet}
  {#snippet actions()}
    <button class="btn-primary" onclick={() => (showForm = !showForm)}>+ new runner</button>
  {/snippet}

  <div class="space-y-4">
    {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}

    {#if showForm}
      <div class="card-quiet" style="max-width: 660px;">
        <span class="label-cap" style="display: block; margin-bottom: 8px;">New runner — an agent on a schedule</span>
        <div class="grid grid-cols-2 gap-4">
          <input class="field" bind:value={fName} placeholder="name (unique)" aria-label="runner name" />
          <select class="field" bind:value={fAgent} aria-label="agent">
            {#each agents as a}<option value={a.name}>{a.name}</option>{/each}
          </select>
          <select class="field" bind:value={fKind} aria-label="trigger type">
            <option value="cron">Recurring (cron)</option>
            <option value="once">Once (one-shot)</option>
          </select>
          {#if fKind === 'once'}
            <input class="field" type="datetime-local" bind:value={fRunAt} aria-label="run date and time" />
          {:else}
            <CronField bind:value={fCron} />
          {/if}
          <select class="field" bind:value={fProject} aria-label="attach to project">
            <option value="">standalone (no project)</option>
            {#each projects as p}<option value={p.id}>attach to: {p.name}</option>{/each}
          </select>
          <textarea class="field" bind:value={fTask} rows="4" aria-label="task prompt"
            placeholder={fKind === 'once' ? 'prompt to run once — what should the agent do?' : 'prompt to run each tick — what should the agent do?'}
            style="grid-column: span 2; resize: vertical; line-height: 1.5;"></textarea>
          <label class="mode-check" title="An evaluator reviews each output against the task; a failed verdict re-runs the agent once with the reviewer's feedback.">
            <input type="checkbox" bind:checked={fVerify} />
            <span><strong>verify</strong> — review each output, retry once on a bad one</span>
          </label>
          <label class="mode-check" title="Each run sees the previous run's report and reports only what's new or changed — ideal for recurring news/research runners.">
            <input type="checkbox" bind:checked={fDelta} />
            <span><strong>delta</strong> — report only what changed since last run</span>
          </label>
        </div>
        <div style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px;">
          <button class="btn-ghost" onclick={() => (showForm = false)}>cancel</button>
          <button class="btn-primary" onclick={create}>create runner</button>
        </div>
        <p class="text-muted" style="font-size: 11px; margin-top: 8px;">
          Runners fire inside <code>bran serve</code>. Attaching to a project runs the agent with that project's memory.
        </p>
      </div>
    {/if}

    {#if !loaded}
      <Skeleton rows={5} />
    {:else if !runners.length}
      <EmptyState title="no runners" hint="a runner is an agent on a schedule — your morning briefing, your weekly digest">
        <button class="btn-primary" onclick={() => (showForm = true)}>+ create your first runner</button>
      </EmptyState>
    {:else}
      <div style="overflow-x: auto;">
        <table class="bran-table" style="min-width: 780px;">
          <thead>
            <tr>
              <th>Name</th><th>Agent</th><th>Trigger</th><th>Next</th><th>Attached</th><th>State</th><th>Task</th><th></th>
            </tr>
          </thead>
          <tbody>
            {#each runners as r}
              <tr>
                <td>
                  <a href={href('/runners/' + encodeURIComponent(r.name))} use:link style="color: var(--fg-bright); text-decoration: none; font-weight: 500;">{r.name}</a>
                  {#if lastRun[r.id]?.status === 'failed'}
                    <span class="fail-dot" title="last run failed: {lastRun[r.id].error ?? 'unknown error'}">●</span>
                  {/if}
                </td>
                <td class="mono" style="color: var(--accent-soft);">
                  {r.agent}
                  {#if r.verify}<span class="mode-tag" title="outputs are reviewed by an evaluator">v</span>{/if}
                  {#if r.delta}<span class="mode-tag" title="reports only what changed since the last run">Δ</span>{/if}
                </td>
                <td class="mono" style="color: var(--fg-dim);">{r.run_at ? 'once' : r.cron}</td>
                <td style="color: var(--fg-dim); white-space: nowrap;">{r.enabled && r.next_run ? localDateTime(r.next_run) : '—'}</td>
                <td style="color: var(--fg-dim);">{projName(r.project_id) ?? '—'}</td>
                <td><button class="state-btn" class:on={r.enabled} onclick={() => toggle(r)} title={r.enabled ? 'pause' : 'resume'}>{r.enabled ? 'on' : 'paused'}</button></td>
                <td style="color: var(--fg-dim); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.task}</td>
                <td><button class="btn-ghost" onclick={() => remove(r.name)} aria-label="delete runner {r.name}">×</button></td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
</Page>

<style>
  .state-btn {
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid var(--border2);
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    transition: all 0.12s var(--transition);
  }
  .state-btn.on { color: var(--accent-soft); border-color: var(--accent-soft); background: var(--accent-glow); }
  .state-btn:hover { border-color: var(--muted); }
  .fail-dot {
    color: var(--red);
    font-size: 9px;
    margin-left: 6px;
    vertical-align: middle;
    cursor: help;
  }
  .mode-check {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 12px;
    color: var(--fg-dim);
    cursor: pointer;
  }
  .mode-check input { cursor: pointer; }
  .mode-check strong { color: var(--fg-bright); font-weight: 500; }
  .mode-tag {
    display: inline-block;
    margin-left: 6px;
    padding: 0 5px;
    border-radius: 4px;
    background: var(--accent-glow);
    color: var(--accent-soft);
    font-size: 10px;
    cursor: help;
  }
</style>
