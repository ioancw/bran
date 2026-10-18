<script lang="ts">
  // Runner detail: a managed agent's trigger + task, a "Run now" control, and
  // its run history.
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { fmtCost, relativeTime, localDateTime, toDatetimeLocal } from '../lib/time'
  import { errorText } from '../lib/errors'
  import { toast } from '../lib/toast.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { validateTrigger, nowLocal, describeCron } from '../lib/schedule'
  import { sourceLabel, sourceHint } from '../lib/labels'
  import EmptyState from '../components/EmptyState.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import CronField from '../components/CronField.svelte'
  import Icon from '../components/Icon.svelte'
  import type { AgentInfo, HealthSnapshot, ProjectSummary, RunRecord, ScheduleRecord } from '../lib/types'

  let { runnerName }: { runnerName: string } = $props()

  let runner = $state<ScheduleRecord | null>(null)
  let health = $state<HealthSnapshot | null>(null)
  const spend30 = $derived(runner && health ? (health.spend.runners_30d[runner.id] ?? null) : null)
  let runs = $state<RunRecord[]>([])
  let projects = $state<ProjectSummary[]>([])
  let agents = $state<AgentInfo[]>([])
  let error = $state<string | null>(null)
  let loaded = $state(false)
  let firing = $state(false)
  let busy = $state(false)
  const uid = $props.id()

  // --- edit mode ---
  let editing = $state(false)
  let saving = $state(false)
  let eAgent = $state('')
  let eTask = $state('')
  let eKind = $state<'cron' | 'once'>('cron')
  let eCron = $state('')
  let eRunAt = $state('')
  let eVerify = $state(false)
  let eDelta = $state(false)
  let eAlert = $state('')
  let editError = $state('')

  const projName = (id: string | null) => (id ? (projects.find((p) => p.id === id)?.name ?? id) : null)

  async function load() {
    error = null // clear a stale banner so a recovered load shows clean
    try {
      const [schedules, ps, ag] = await Promise.all([api.schedules(), api.projects(), api.agents()])
      runner = schedules.find((s) => s.name === runnerName) ?? null
      projects = ps
      agents = ag
      if (runner) runs = await api.runs({ schedule_id: runner.id, limit: 50 })
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
    try {
      health = await api.health()
    } catch {
      /* the cost rows simply don't show */
    }
  }

  function startEdit() {
    if (!runner) return
    eAgent = runner.agent
    eTask = runner.task ?? ''
    eKind = runner.run_at ? 'once' : 'cron'
    eCron = runner.cron ?? ''
    eRunAt = toDatetimeLocal(runner.run_at) // stored UTC ISO → local picker value
    eVerify = !!runner.verify
    eDelta = !!runner.delta
    eAlert = runner.alert ?? ''
    editError = ''
    editing = true
  }
  async function saveEdit() {
    if (!runner || saving) return
    // Same client-side checks as the create form — an empty cron or a blank/past
    // date is catchable here with human copy, not a server round-trip.
    const invalid = validateTrigger(eKind, eCron, eRunAt)
    if (invalid) {
      editError = invalid
      return
    }
    saving = true
    editError = ''
    try {
      const fields =
        eKind === 'once'
          ? {
              agent: eAgent,
              task: eTask,
              run_at: new Date(eRunAt).toISOString(),
              verify: eVerify,
              delta: eDelta,
              alert: eAlert.trim(),
            }
          : {
              agent: eAgent,
              task: eTask,
              cron: eCron.trim(),
              verify: eVerify,
              delta: eDelta,
              alert: eAlert.trim(),
            }
      runner = await api.updateSchedule(runner.name, fields)
      editing = false
      toast('Runner saved', 'ok')
    } catch (e) {
      editError = errorText(e)
    } finally {
      saving = false
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
      const r = await api.newRun(runner.agent, runner.task || `Run ${runner.agent}`, {
        schedule_id: runner.id,
      })
      navigate('/runs/' + r.id)
    } catch (e) {
      // Action failure → toast; the page banner (with its reload-retry) is for
      // load failures only.
      toast(errorText(e), 'err')
      firing = false
    }
  }
  async function toggleEnabled() {
    if (!runner || busy) return
    busy = true
    // Optimistic flip; reconcile with the server, revert + toast on error.
    const prev = runner
    runner = { ...runner, enabled: !runner.enabled }
    try {
      runner = await api.setScheduleEnabled(prev.name, !prev.enabled)
      toast(`${runner.enabled ? 'Resumed' : 'Paused'} ${runner.name}`, 'ok')
    } catch (e) {
      runner = prev
      toast(errorText(e), 'err')
    } finally {
      busy = false
    }
  }
  async function remove() {
    if (!runner) return
    if (
      !(await confirmDialog(
        `Delete runner "${runner.name}"? Its schedule stops. Past runs and outputs are kept.`,
        'Delete runner',
      ))
    )
      return
    try {
      await api.deleteSchedule(runner.name)
      toast(`Deleted runner ${runner.name}`, 'ok')
    } catch (e) {
      toast(errorText(e), 'err')
      return
    }
    navigate('/runners')
  }
</script>

<Page title={runnerName}>
  {#snippet subtitle()}Runner{/snippet}
  {#snippet actions()}
    {#if runner && !editing}
      <button class="btn-outline" onclick={startEdit}><Icon name="pencil" size={13} />Edit</button>
      <button class="btn-ghost danger" onclick={remove}><Icon name="trash" size={13} />Delete</button>
    {/if}
  {/snippet}

  {#if error}
    <!-- A failed load is not "unknown runner" — don't show both, and offer retry. -->
    <ErrorBanner {error} onretry={load} />
  {:else if !loaded}
    <Skeleton rows={5} />
  {:else if !runner}
    <EmptyState title="Runner not found" hint="It may have been deleted. Check the runners list." icon="bot">
      <a href={href('/runners')} use:link class="btn-outline">All runners</a>
    </EmptyState>
  {:else}
    <div class="grid grid-cols-3 gap-6">
      <!-- Main: task + run history -->
      <div class="col-span-2 space-y-6">
        {#if editing}
          <div class="card">
            <h2 class="card-title">Edit runner</h2>
            <div class="space-y-4">
              <div class="fld">
                <label for="{uid}-task">Task</label>
                <textarea
                  id="{uid}-task"
                  class="field"
                  rows="6"
                  bind:value={eTask}
                  placeholder="What the agent should do each time it runs"
                  style="resize: vertical; line-height: 1.5;"></textarea>
              </div>
              <div class="grid grid-cols-2 gap-4">
                <div class="fld">
                  <label for="{uid}-agent">Agent</label>
                  <select id="{uid}-agent" class="field" bind:value={eAgent}>
                    {#each agents as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
                  </select>
                </div>
                <div class="fld">
                  {#if eKind === 'once'}
                    <label for="{uid}-runat">Run at</label>
                    <input
                      id="{uid}-runat"
                      class="field"
                      type="datetime-local"
                      bind:value={eRunAt}
                      min={nowLocal()}
                    />
                  {:else}
                    <span class="fld-label">Schedule</span>
                    <CronField bind:value={eCron} />
                  {/if}
                </div>
              </div>
              <div class="space-y-3">
                <label class="mode-check">
                  <input type="checkbox" bind:checked={eVerify} />
                  <span>
                    <strong>Verify</strong>
                    <span class="fld-help"
                      >An evaluator reviews each output against the task and re-runs once on a bad verdict.</span
                    >
                  </span>
                </label>
                <label class="mode-check">
                  <input type="checkbox" bind:checked={eDelta} />
                  <span>
                    <strong>Delta</strong>
                    <span class="fld-help"
                      >Each run sees the previous report and reports only what changed.</span
                    >
                  </span>
                </label>
                <div class="fld">
                  <label for="{uid}-alert">Alert when <span class="text-muted">(optional)</span></label>
                  <input
                    id="{uid}-alert"
                    class="field"
                    bind:value={eAlert}
                    placeholder="A significance bar. Blank = off."
                  />
                </div>
              </div>
              {#if editError}<div role="alert" class="form-error">{editError}</div>{/if}
              <div class="form-actions">
                <button class="btn-ghost" disabled={saving} onclick={() => (editing = false)}>Cancel</button>
                <button class="btn-primary" disabled={saving} onclick={saveEdit}
                  >{saving ? 'Saving…' : 'Save'}</button
                >
              </div>
            </div>
          </div>
        {:else}
          <div class="card">
            <h2 class="card-title">Task</h2>
            <div class="task-text">{runner.task || '—'}</div>
            <div style="display: flex; justify-content: flex-end; margin-top: 14px;">
              <button class="btn-primary" disabled={firing} onclick={runNow}>
                <Icon name="play" size={13} />{firing ? 'Starting…' : 'Run now'}
              </button>
            </div>
          </div>
        {/if}

        <section>
          <h2 class="sec-title">Runs <span class="text-muted">{runs.length}</span></h2>
          {#if runs.length}
            <div class="run-list">
              {#each runs.slice(0, 20) as r (r.id)}
                <a href={href('/runs/' + r.id)} use:link class="run-row">
                  <span class="src src-{r.source}" title={sourceHint(r.source)}>{sourceLabel(r.source)}</span>
                  <StatusBadge status={r.status} />
                  <span class="run-task">{r.task}</span>
                  <span class="run-meta" title={localDateTime(r.started_at)}
                    >{relativeTime(r.started_at)} · {fmtCost(r.total_cost_usd)}</span
                  >
                </a>
              {/each}
            </div>
          {:else}
            <p class="text-muted" style="font-size: 13px;">No runs yet. Use Run now to fire the first one.</p>
          {/if}
        </section>
      </div>

      <!-- Right: trigger + config -->
      <aside class="col-span-1 space-y-4">
        <div class="card">
          <h2 class="card-title">Trigger</h2>
          <dl class="defs">
            {#if runner.run_at}
              <dt>Once</dt>
              <dd>{localDateTime(runner.run_at)}</dd>
            {:else}
              <dt>Schedule</dt>
              <dd>
                {describeCron(runner.cron ?? '')}
                {#if describeCron(runner.cron ?? '') !== runner.cron}
                  <span class="raw-cron mono">{runner.cron}</span>
                {/if}
              </dd>
            {/if}
            <dt>Next run</dt>
            <dd class="text-dim">
              {runner.enabled && runner.next_run ? localDateTime(runner.next_run) : '—'}
            </dd>
            <dt>State</dt>
            <dd>
              <span class="state-cell">
                <span class="state-word"
                  ><span class="dot" class:ok={runner.enabled}></span>{runner.enabled ? 'On' : 'Paused'}</span
                >
                <button class="btn-outline btn-sm" disabled={busy} onclick={toggleEnabled}>
                  {#if runner.enabled}<Icon name="pause" size={12} />Pause{:else}<Icon
                      name="play"
                      size={12}
                    />Resume{/if}
                </button>
              </span>
            </dd>
          </dl>
        </div>

        <div class="card">
          <h2 class="card-title">Configuration</h2>
          <dl class="defs">
            <dt>Agent</dt>
            <dd><a href={href('/agents/' + encodeURIComponent(runner.agent))} use:link>{runner.agent}</a></dd>
            <dt>Project</dt>
            <dd>
              {#if runner.project_id}
                <a href={href('/projects/' + runner.project_id)} use:link>{projName(runner.project_id)}</a>
              {:else}
                <span class="text-muted">Standalone</span>
              {/if}
            </dd>
            <dt>Modes</dt>
            <dd>
              {#if runner.verify || runner.delta}
                <span class="mode-pills">
                  {#if runner.verify}<span class="pill mute">Verified</span>{/if}
                  {#if runner.delta}<span class="pill accent">Delta</span>{/if}
                </span>
              {:else}
                <span class="text-muted">None</span>
              {/if}
            </dd>
            {#if runner.alert}
              <dt>Alert when</dt>
              <dd><span class="alert-line"><Icon name="alert-triangle" size={13} />{runner.alert}</span></dd>
            {/if}
            {#if health}
              <dt>30-day cost</dt>
              <dd class="num">{fmtCost(spend30?.cost ?? 0)}</dd>
              <dt>30-day runs</dt>
              <dd class="num">{spend30?.runs ?? 0}</dd>
            {/if}
          </dl>
        </div>
      </aside>
    </div>
  {/if}
</Page>

<style>
  .card-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg-bright);
    margin-bottom: 10px;
  }
  .sec-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg-bright);
    margin-bottom: 8px;
  }
  .task-text {
    font-size: 14px;
    line-height: 1.55;
    color: var(--fg);
    white-space: pre-wrap;
  }

  .defs {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 8px 14px;
    margin: 0;
    font-size: 13px;
  }
  .defs dt {
    font-size: 12px;
    color: var(--muted);
    padding-top: 1px;
  }
  .defs dd {
    margin: 0;
    min-width: 0;
    color: var(--fg);
  }
  .raw-cron {
    display: block;
    font-size: 11px;
    color: var(--muted);
    margin-top: 2px;
  }
  .state-cell {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .state-word {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 500;
    color: var(--fg-dim);
  }
  .mode-pills {
    display: inline-flex;
    gap: 6px;
  }
  .alert-line {
    display: inline-flex;
    align-items: flex-start;
    gap: 6px;
    color: var(--red);
  }

  .run-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .run-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: inherit;
    transition: background var(--dur-1) var(--transition);
  }
  .run-row:hover {
    background: var(--surface2);
    text-decoration: none;
  }
  .run-task {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    color: var(--fg-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .run-meta {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }

  .fld {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
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
  }
  .form-actions {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
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
</style>
