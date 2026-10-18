<script lang="ts">
  // Runners = standalone managed agents (scheduled, headless). Not owned by a
  // project; optionally *attached* to one to borrow its context/visibility.
  import { api } from '../lib/api'
  import { router, href, link } from '../lib/router.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { errorText } from '../lib/errors'
  import { toast } from '../lib/toast.svelte'
  import { localDateTime, fmtCost } from '../lib/time'
  import { validateTrigger, nowLocal, describeCron } from '../lib/schedule'
  import EmptyState from '../components/EmptyState.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import CronField from '../components/CronField.svelte'
  import Icon from '../components/Icon.svelte'
  import type { AgentInfo, HealthSnapshot, ProjectSummary, RunRecord, ScheduleRecord } from '../lib/types'

  let runners = $state<ScheduleRecord[]>([])
  // 30-day cost/run counts per runner (health.spend.runners_30d); optional.
  let health = $state<HealthSnapshot | null>(null)
  const spend30 = (id: string) => health?.spend.runners_30d[id] ?? null
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
  let fAlert = $state('') // significance bar; '' = normal (non-sensing) runner
  let creating = $state(false)
  let formError = $state<string | null>(null)
  const uid = $props.id()

  async function load() {
    error = null // clear a stale banner so a recovered refresh shows clean
    try {
      ;[runners, agents, projects, recentRuns] = await Promise.all([
        api.schedules(),
        api.agents(),
        api.projects(),
        api.runs({ limit: 200, exclude_chats: true }),
      ])
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
    try {
      health = await api.health()
    } catch {
      /* cost columns show a dash */
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

  const projName = (id: string | null) => (id ? (projects.find((p) => p.id === id)?.name ?? id) : null)

  async function create(e?: Event) {
    e?.preventDefault()
    if (creating) return
    if (!fName.trim()) {
      formError = 'Give the runner a name.'
      return
    }
    if (!fTask.trim()) {
      formError = 'Describe the task: what should the agent do each run?'
      return
    }
    const fields: {
      name: string
      agent: string
      task: string
      project_id?: string
      cron?: string
      run_at?: string
      verify?: boolean
      delta?: boolean
      alert?: string
    } = {
      name: fName.trim(),
      agent: fAgent,
      task: fTask,
      project_id: fProject || undefined,
      verify: fVerify,
      delta: fDelta,
      alert: fAlert.trim(),
    }
    const invalid = validateTrigger(fKind, fCron, fRunAt)
    if (invalid) {
      formError = invalid
      return
    }
    if (fKind === 'once')
      fields.run_at = new Date(fRunAt).toISOString() // local picker → UTC
    else fields.cron = fCron.trim()
    formError = null
    creating = true
    try {
      await api.newSchedule(fields)
      toast(`Created runner ${fields.name}`, 'ok')
    } catch (err) {
      formError = errorText(err)
      return
    } finally {
      creating = false
    }
    fName = ''
    fTask = ''
    fProject = ''
    fRunAt = ''
    fVerify = false
    fDelta = false
    fAlert = ''
    showForm = false
    await load()
  }
  async function remove(name: string) {
    if (
      !(await confirmDialog(
        `Delete runner "${name}"? Its schedule stops. Past runs and outputs are kept.`,
        'Delete runner',
      ))
    )
      return
    try {
      await api.deleteSchedule(name)
      toast(`Deleted runner ${name}`, 'ok')
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
      toast(`${updated.enabled ? 'Resumed' : 'Paused'} ${r.name}`, 'ok')
    } catch (e) {
      runners = runners.map((x) => (x.name === r.name ? { ...x, enabled: r.enabled } : x))
      toast(errorText(e), 'err')
    }
  }
</script>

<Page title="Runners">
  {#snippet subtitle()}{runners.length} scheduled {runners.length === 1 ? 'agent' : 'agents'}{/snippet}
  {#snippet actions()}
    <button class="btn-primary" onclick={() => (showForm = !showForm)}>
      <Icon name="plus" size={14} stroke={2.2} />New runner
    </button>
  {/snippet}

  <div class="space-y-4">
    {#if error}<ErrorBanner {error} onretry={load} />{/if}

    {#if showForm}
      <form class="card-quiet runner-form" onsubmit={create}>
        <h2 class="form-title">New runner</h2>
        <p class="form-hint">An agent on a schedule. Runners fire inside <code>bran serve</code>.</p>
        <div class="grid grid-cols-2 gap-4">
          <div class="fld">
            <label for="{uid}-name">Name</label>
            <input id="{uid}-name" class="field" bind:value={fName} placeholder="morning-brief" />
          </div>
          <div class="fld">
            <label for="{uid}-agent">Agent</label>
            <select id="{uid}-agent" class="field" bind:value={fAgent}>
              {#each agents as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
            </select>
          </div>
          <div class="fld">
            <label for="{uid}-kind">Trigger</label>
            <select id="{uid}-kind" class="field" bind:value={fKind}>
              <option value="cron">Recurring</option>
              <option value="once">Once</option>
            </select>
          </div>
          <div class="fld">
            {#if fKind === 'once'}
              <label for="{uid}-runat">Run at</label>
              <input
                id="{uid}-runat"
                class="field"
                type="datetime-local"
                bind:value={fRunAt}
                min={nowLocal()}
              />
            {:else}
              <span class="fld-label">Schedule</span>
              <CronField bind:value={fCron} />
            {/if}
          </div>
          <div class="fld">
            <label for="{uid}-project">Project</label>
            <select id="{uid}-project" class="field" bind:value={fProject}>
              <option value="">Standalone (no project)</option>
              {#each projects as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
            </select>
            <span class="fld-help">Attaching runs the agent with that project's instructions and memory.</span
            >
          </div>
          <div class="fld span2">
            <label for="{uid}-task">Task</label>
            <textarea
              id="{uid}-task"
              class="field"
              bind:value={fTask}
              rows="4"
              placeholder={fKind === 'once'
                ? 'What should the agent do when it runs?'
                : 'What should the agent do each time it runs?'}
              style="resize: vertical; line-height: 1.5;"></textarea>
          </div>
          <label class="mode-check">
            <input type="checkbox" bind:checked={fVerify} />
            <span>
              <strong>Verify</strong>
              <span class="fld-help"
                >An evaluator reviews each output against the task and re-runs once on a bad verdict.</span
              >
            </span>
          </label>
          <label class="mode-check">
            <input type="checkbox" bind:checked={fDelta} />
            <span>
              <strong>Delta</strong>
              <span class="fld-help">Each run sees the previous report and reports only what changed.</span>
            </span>
          </label>
          <div class="fld span2">
            <label for="{uid}-alert">Alert when <span class="text-muted">(optional)</span></label>
            <input
              id="{uid}-alert"
              class="field"
              bind:value={fAlert}
              placeholder="e.g. GBP moves more than 1% intraday"
            />
            <span class="fld-help">A significance bar. A run that crosses it is escalated as an alert.</span>
          </div>
        </div>
        {#if formError}<div role="alert" class="form-error">{formError}</div>{/if}
        <div class="form-actions">
          <button
            type="button"
            class="btn-ghost"
            onclick={() => {
              showForm = false
              formError = null
            }}>Cancel</button
          >
          <button type="submit" class="btn-primary" disabled={creating}
            >{creating ? 'Creating…' : 'Create runner'}</button
          >
        </div>
      </form>
    {/if}

    {#if !loaded}
      <Skeleton rows={5} />
    {:else if !runners.length}
      <EmptyState
        title="No runners yet"
        hint="A runner is an agent on a schedule: a morning briefing, a weekly digest, a one-off job at 3pm."
        icon="bot"
      >
        <button class="btn-primary" onclick={() => (showForm = true)}>
          <Icon name="plus" size={14} stroke={2.2} />Create a runner
        </button>
      </EmptyState>
    {:else}
      <div style="overflow-x: auto;">
        <table class="bran-table" style="min-width: 960px;">
          <thead>
            <tr>
              <th>Name</th><th>Agent</th><th>Trigger</th><th>Next run</th><th class="num">30-day cost</th><th
                class="num">Runs</th
              ><th>Project</th><th>State</th><th>Task</th><th></th>
            </tr>
          </thead>
          <tbody>
            {#each runners as r (r.name)}
              {@const last = lastRun[r.id]}
              <tr>
                <td>
                  <span class="name-cell">
                    {#if last?.status === 'failed'}
                      <span class="dot err" title="Last run failed: {last.error ?? 'unknown error'}"></span>
                    {/if}
                    <a href={href('/runners/' + encodeURIComponent(r.name))} use:link class="name-link"
                      >{r.name}</a
                    >
                  </span>
                </td>
                <td>
                  <span class="agent-cell">
                    <span>{r.agent}</span>
                    {#if r.verify}<span class="pill mute" title="Outputs are reviewed by an evaluator"
                        >Verified</span
                      >{/if}
                    {#if r.delta}<span
                        class="pill accent"
                        title="Reports only what changed since the last run">Delta</span
                      >{/if}
                    {#if r.alert}<span class="pill err" title="Alerts when: {r.alert}"
                        ><Icon name="alert-triangle" size={12} />Alert</span
                      >{/if}
                  </span>
                </td>
                <td class="text-dim">
                  {#if r.run_at}
                    <span title={r.run_at}>Once · {localDateTime(r.run_at)}</span>
                  {:else}
                    <span title={r.cron ?? ''} style="white-space: nowrap;">{describeCron(r.cron ?? '')}</span
                    >
                  {/if}
                </td>
                <td class="text-dim" style="white-space: nowrap;"
                  >{r.enabled && r.next_run ? localDateTime(r.next_run) : '—'}</td
                >
                <td class="num mono">{health ? fmtCost(spend30(r.id)?.cost ?? 0) : '—'}</td>
                <td class="num mono">{health ? (spend30(r.id)?.runs ?? 0) : '—'}</td>
                <td class="text-dim">{projName(r.project_id) ?? '—'}</td>
                <td>
                  <span class="state-cell">
                    <span class="state-word"
                      ><span class="dot" class:ok={r.enabled}></span>{r.enabled ? 'On' : 'Paused'}</span
                    >
                    <button class="btn-outline btn-sm" onclick={() => toggle(r)}>
                      {#if r.enabled}<Icon name="pause" size={12} />Pause{:else}<Icon
                          name="play"
                          size={12}
                        />Resume{/if}
                    </button>
                  </span>
                </td>
                <td class="text-dim task-cell" title={r.task}>{r.task}</td>
                <td>
                  <button
                    class="btn-icon danger"
                    onclick={() => remove(r.name)}
                    aria-label="Delete runner {r.name}"
                    title="Delete runner"
                  >
                    <Icon name="x" size={14} />
                  </button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
</Page>

<style>
  .runner-form {
    max-width: 720px;
  }
  .form-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .form-hint {
    font-size: 12.5px;
    color: var(--muted);
    margin: 2px 0 14px;
  }
  .form-hint code {
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .fld {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .fld.span2 {
    grid-column: span 2;
  }
  .fld label,
  .fld-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg);
  }
  .fld-help {
    display: block;
    font-size: 12px;
    color: var(--muted);
    line-height: 1.4;
  }
  .form-error {
    color: var(--red);
    font-size: 12.5px;
    margin-top: 10px;
  }
  .form-actions {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
    margin-top: 14px;
  }
  .mode-check {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: 13px;
    color: var(--fg-dim);
    cursor: pointer;
  }
  .mode-check input {
    cursor: pointer;
    margin-top: 2px;
  }
  .mode-check strong {
    display: block;
    color: var(--fg-bright);
    font-weight: 500;
  }

  .name-cell {
    display: inline-flex;
    align-items: center;
    gap: 7px;
  }
  .name-link {
    color: var(--fg-bright);
    font-weight: 500;
    white-space: nowrap;
  }
  .agent-cell {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .state-cell {
    display: inline-flex;
    align-items: center;
    gap: 10px;
  }
  .state-word {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    font-weight: 500;
    color: var(--fg-dim);
    white-space: nowrap;
  }
  .task-cell {
    max-width: 280px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  @media (max-width: 768px) {
    .fld.span2,
    .runner-form :global(.grid-cols-2) {
      grid-column: auto;
    }
  }
</style>
