<script lang="ts">
  // Runners = standalone managed agents (scheduled, headless). Not owned by a
  // project; optionally *attached* to one to borrow its context/visibility.
  import { api } from '../lib/api'
  import { router, href, link } from '../lib/router.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { errorText } from '../lib/errors'
  import { localDateTime } from '../lib/time'
  import Page from '../components/Page.svelte'
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

  async function load() {
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
    const fields: { name: string; agent: string; task: string; project_id?: string; cron?: string; run_at?: string } = {
      name: fName.trim(), agent: fAgent, task: fTask, project_id: fProject || undefined,
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
    } catch (e) {
      error = String(e)
      return
    }
    fName = ''
    fTask = ''
    fProject = ''
    fRunAt = ''
    showForm = false
    await load()
  }
  async function remove(name: string) {
    if (!(await confirmDialog(`Delete runner "${name}"?`))) return
    await api.deleteSchedule(name)
    await load()
  }
  async function toggle(r: ScheduleRecord) {
    try {
      const updated = await api.setScheduleEnabled(r.name, !r.enabled)
      runners = runners.map((x) => (x.name === r.name ? updated : x))
    } catch (e) {
      error = String(e)
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
          <input class="field" bind:value={fName} placeholder="name (unique)" />
          <select class="field" bind:value={fAgent}>
            {#each agents as a}<option value={a.name}>{a.name}</option>{/each}
          </select>
          <select class="field" bind:value={fKind}>
            <option value="cron">Recurring (cron)</option>
            <option value="once">Once (one-shot)</option>
          </select>
          {#if fKind === 'once'}
            <input class="field" type="datetime-local" bind:value={fRunAt} />
          {:else}
            <CronField bind:value={fCron} />
          {/if}
          <select class="field" bind:value={fProject}>
            <option value="">standalone (no project)</option>
            {#each projects as p}<option value={p.id}>attach to: {p.name}</option>{/each}
          </select>
          <textarea class="field" bind:value={fTask} rows="4"
            placeholder={fKind === 'once' ? 'prompt to run once — what should the agent do?' : 'prompt to run each tick — what should the agent do?'}
            style="grid-column: span 2; resize: vertical; line-height: 1.5;"></textarea>
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
      <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
    {:else if !runners.length}
      <div class="empty-state"><h3>no runners</h3><p class="cta">create one to run an agent on a schedule</p></div>
    {:else}
      <div class="card flush" style="overflow-x: auto;">
        <table style="width: 100%; border-collapse: collapse; min-width: 780px;">
          <thead>
            <tr class="label-cap" style="text-align: left;">
              <th style="padding: 10px 14px;">Name</th><th>Agent</th><th>Trigger</th><th>Next</th><th>Attached</th><th>State</th><th>Task</th><th></th>
            </tr>
          </thead>
          <tbody>
            {#each runners as r}
              <tr style="border-top: 1px solid var(--border);">
                <td style="padding: 10px 14px;">
                  <a href={href('/runners/' + encodeURIComponent(r.name))} use:link class="text-bright" style="text-decoration: none; font-weight: 500;">{r.name}</a>
                  {#if lastRun[r.id]?.status === 'failed'}
                    <span class="fail-dot" title="last run failed: {lastRun[r.id].error ?? 'unknown error'}">●</span>
                  {/if}
                </td>
                <td class="mono text-accent-soft">{r.agent}</td>
                <td class="mono text-dim">{r.run_at ? 'once' : r.cron}</td>
                <td class="text-dim" style="font-size: 12px; white-space: nowrap;">{r.enabled && r.next_run ? localDateTime(r.next_run) : '—'}</td>
                <td class="text-dim">{projName(r.project_id) ?? '—'}</td>
                <td><button class="state-btn" class:on={r.enabled} onclick={() => toggle(r)} title={r.enabled ? 'pause' : 'resume'}>{r.enabled ? 'on' : 'paused'}</button></td>
                <td class="text-dim" style="max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.task}</td>
                <td><button class="btn-ghost" onclick={() => remove(r.name)}>×</button></td>
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
</style>
