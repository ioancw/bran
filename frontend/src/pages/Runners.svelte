<script lang="ts">
  // Runners = standalone managed agents (scheduled, headless). Not owned by a
  // project; optionally *attached* to one to borrow its context/visibility.
  //
  // Creating one starts from a sentence ("every weekday at 7, brief me on FX
  // and fact-check it") or a template; bran drafts the form, the user checks
  // it and creates it. The full form is always one click away.
  import { api } from '../lib/api'
  import { router, href, link } from '../lib/router.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { errorText } from '../lib/errors'
  import { toast } from '../lib/toast.svelte'
  import { localDateTime, fmtCost } from '../lib/time'
  import { validateTrigger, nowLocal, nextHourLocal, describeCron } from '../lib/schedule'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import CronField from '../components/CronField.svelte'
  import Icon from '../components/Icon.svelte'
  import type {
    AgentInfo,
    HealthSnapshot,
    ProjectSummary,
    RunRecord,
    RunnerDraft,
    ScheduleRecord,
  } from '../lib/types'

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

  // --- New runner: describe → draft → check → create ----------------------
  let showForm = $state(false)
  let stage = $state<'describe' | 'form'>('describe')
  let draftText = $state('')
  let drafting = $state(false)
  let draftError = $state<string | null>(null)
  let draftNote = $state('') // what the draft decided, shown above the form

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

  // Templates: the runners most people make first. Each fills the form; the
  // task text leaves a <blank> where the user's own subject goes.
  type Template = {
    key: string
    icon: string
    title: string
    desc: string
    name: string
    agent: string
    kind: 'cron' | 'once'
    schedule?: string
    task: string
    verify?: boolean
    delta?: boolean
    alert?: string
  }
  const TEMPLATES: Template[] = [
    {
      key: 'brief',
      icon: 'sun',
      title: 'Morning brief',
      desc: 'A finance briefing on your desk before you are, fact-checked before it lands.',
      name: 'morning-brief',
      agent: 'finance-news',
      kind: 'cron',
      schedule: 'every weekday at 7am',
      task: 'Produce a structured morning finance briefing covering overnight markets, rates, FX, commodities and macro.',
      verify: true,
    },
    {
      key: 'digest',
      icon: 'file-text',
      title: 'Weekly digest',
      desc: 'What changed this week on a topic, with sources, and only what is new.',
      name: 'weekly-digest',
      agent: 'research',
      kind: 'cron',
      schedule: 'every Monday at 8am',
      task: 'Round up what changed this week in <topic>: the five developments that matter most, each with a source, and what they imply.',
      delta: true,
    },
    {
      key: 'watch',
      icon: 'radar',
      title: 'Watch and alert',
      desc: 'Checks every couple of hours and only raises its hand when the bar is crossed.',
      name: 'gbp-watch',
      agent: 'research',
      kind: 'cron',
      schedule: 'every 2 hours',
      task: 'Check the latest GBP/USD and EUR/GBP moves and the reasons behind them.',
      alert: 'GBP moves more than 1% intraday',
    },
    {
      key: 'factcheck',
      icon: 'shield-check',
      title: 'Fact-check a document',
      desc: 'Verify every claim in a file against the web and correct it in place. Runs once.',
      name: 'fact-check',
      agent: 'orchestrator',
      kind: 'once',
      task: 'Run the fact-check workflow on <path to the document> and correct it in place.',
    },
  ]

  const tplMeta = (t: Template) =>
    t.kind === 'once' ? `${t.agent} · once` : t.schedule ? `${t.agent} · ${t.schedule}` : t.agent

  function applyTemplate(t: Template) {
    fName = t.name
    fAgent = agents.some((a) => a.name === t.agent) ? t.agent : 'orchestrator'
    fKind = t.kind
    if (t.kind === 'once') fRunAt = nextHourLocal()
    else fCron = t.schedule ?? 'every day at 8am'
    fTask = t.task
    fVerify = !!t.verify
    fDelta = !!t.delta
    fAlert = t.alert ?? ''
    draftNote = `Template: ${t.title}. Replace anything in angle brackets, then create.`
    formError = null
    stage = 'form'
  }

  function applyDraft(d: RunnerDraft) {
    fName = d.name
    fAgent = agents.some((a) => a.name === d.agent) ? d.agent : 'orchestrator'
    fKind = d.kind
    if (d.kind === 'once') fRunAt = d.run_at || nextHourLocal()
    else fCron = d.cron || d.schedule || 'every day at 8am'
    fTask = d.task
    fProject = d.project_id || fProject
    fVerify = d.verify
    fDelta = d.delta
    fAlert = d.alert
    const bits = [`${d.agent} agent`]
    if (d.kind === 'once') bits.push('runs once')
    else if (d.human) bits.push(d.human)
    if (d.verify) bits.push('verified')
    if (d.delta) bits.push('changes only')
    if (d.alert) bits.push(`alerts when ${d.alert}`)
    draftNote = `Drafted from your sentence: ${bits.join(', ')}. Check it, then create.`
    if (d.schedule_error) draftNote += ` The schedule needs a hand: ${d.schedule_error}`
    formError = null
    stage = 'form'
  }

  async function draft(e?: Event) {
    e?.preventDefault()
    const text = draftText.trim()
    if (drafting) return
    if (text.length < 8) {
      draftError = 'Say what the runner should do and when.'
      return
    }
    draftError = null
    drafting = true
    try {
      applyDraft(await api.draftSchedule(text))
    } catch (err) {
      draftError = errorText(err)
    } finally {
      drafting = false
    }
  }
  function onDraftKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void draft()
    }
  }
  function startOver() {
    stage = 'describe'
    draftNote = ''
    formError = null
  }
  function closePanel() {
    showForm = false
    startOver()
  }

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

  // Prefill from the URL: /runners?agent=research (from the Agents page) or
  // /runners?project=<id> (from a project's setup checklist).
  $effect(() => {
    const a = router.route.query.get('agent')
    const p = router.route.query.get('project')
    if (a) {
      fAgent = a
      showForm = true
      stage = 'form'
    }
    if (p) {
      fProject = p
      showForm = true
    }
  })

  const projName = (id: string | null) => (id ? (projects.find((p) => p.id === id)?.name ?? id) : null)
  const panelOpen = $derived(showForm || (loaded && !runners.length))

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
    if (/<[^>]+>/.test(fTask)) {
      formError = 'Replace the <angle-bracket> placeholder in the task with your own subject.'
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
    draftText = ''
    closePanel()
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
    {#if runners.length}
      <button class="btn-primary" onclick={() => (showForm ? closePanel() : (showForm = true))}>
        <Icon name="plus" size={14} stroke={2.2} />New runner
      </button>
    {/if}
  {/snippet}

  <div class="space-y-4">
    {#if error}<ErrorBanner {error} onretry={load} />{/if}

    {#if !loaded}
      <Skeleton rows={5} />
    {:else if panelOpen}
      <section class="new-runner" class:first={!runners.length}>
        {#if stage === 'describe'}
          <div class="nr-intro">
            <h2 class="nr-title">{runners.length ? 'New runner' : 'Put an agent on a schedule'}</h2>
            <p class="nr-hint">
              Say what should happen and when. bran drafts the runner from your sentence; you check it and
              create it.
            </p>
          </div>
          <form class="describe" onsubmit={draft}>
            <textarea
              class="describe-input"
              bind:value={draftText}
              rows="2"
              placeholder="Every weekday at 7, brief me on FX and fact-check it"
              aria-label="Describe the runner"
              onkeydown={onDraftKeydown}
              disabled={drafting}></textarea>
            <div class="describe-foot">
              <span class="describe-help">Enter to draft, Shift+Enter for a new line</span>
              <button type="submit" class="btn-primary" disabled={drafting}>
                {#if drafting}<Icon name="loader" size={14} class="spin" />Drafting…{:else}<Icon
                    name="wand"
                    size={14}
                  />Draft runner{/if}
              </button>
            </div>
          </form>
          {#if draftError}<div role="alert" class="form-error">{draftError}</div>{/if}

          <div class="label-cap tpl-label">Or start from a template</div>
          <div class="templates">
            {#each TEMPLATES as t (t.key)}
              <button class="tpl" onclick={() => applyTemplate(t)}>
                <span class="tpl-ico"><Icon name={t.icon} size={16} /></span>
                <span class="tpl-title">{t.title}</span>
                <span class="tpl-desc">{t.desc}</span>
                <span class="tpl-meta">{tplMeta(t)}</span>
              </button>
            {/each}
          </div>
          <div class="nr-foot">
            <button class="btn-text" onclick={() => (stage = 'form')}>Fill in the form yourself</button>
            {#if runners.length}
              <button class="btn-ghost" onclick={closePanel}>Cancel</button>
            {/if}
          </div>
        {:else}
          <form class="runner-form" onsubmit={create}>
            <div class="nr-intro">
              <h2 class="nr-title">New runner</h2>
              {#if draftNote}
                <p class="draft-note"><Icon name="wand" size={13} />{draftNote}</p>
              {:else}
                <p class="nr-hint">An agent on a schedule. Runners fire inside the server.</p>
              {/if}
            </div>
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
                <span class="fld-help"
                  >Attaching runs the agent with that project's instructions and memory.</span
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
                  <span class="fld-help"
                    >Each run sees the previous report and reports only what changed.</span
                  >
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
                <span class="fld-help"
                  >A significance bar. A run that crosses it is escalated as an alert.</span
                >
              </div>
            </div>
            {#if formError}<div role="alert" class="form-error">{formError}</div>{/if}
            <div class="form-actions">
              <button type="button" class="btn-text" onclick={startOver}>Start over</button>
              <span class="ml-auto"></span>
              {#if runners.length}
                <button type="button" class="btn-ghost" onclick={closePanel}>Cancel</button>
              {/if}
              <button type="submit" class="btn-primary" disabled={creating}
                >{creating ? 'Creating…' : 'Create runner'}</button
              >
            </div>
          </form>
        {/if}
      </section>
    {/if}

    {#if loaded && runners.length}
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
  /* The new-runner panel: a quiet surface when runners exist, the page's
     whole opening when none do. */
  .new-runner {
    background: var(--surface2);
    border-radius: var(--radius-lg);
    padding: 20px 22px 18px;
    max-width: 820px;
  }
  .new-runner.first {
    max-width: 760px;
    margin: 32px auto 0;
    padding: 28px 28px 22px;
  }
  .nr-intro {
    margin-bottom: 14px;
  }
  .nr-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .first .nr-title {
    font-size: 22px;
    letter-spacing: -0.01em;
  }
  .nr-hint {
    font-size: 13px;
    color: var(--muted);
    margin-top: 4px;
    line-height: 1.45;
  }
  .first .nr-hint {
    font-size: 14px;
    max-width: 52ch;
  }
  .draft-note {
    display: flex;
    align-items: flex-start;
    gap: 7px;
    font-size: 13px;
    color: var(--fg-dim);
    margin-top: 6px;
    line-height: 1.45;
  }
  .draft-note :global(svg) {
    color: var(--accent);
    flex-shrink: 0;
    margin-top: 3px;
  }

  /* The sentence box: one surface, the button inside its foot. */
  .describe {
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: var(--radius-lg);
    padding: 12px 12px 10px;
  }
  .describe:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
  }
  .describe-input {
    width: 100%;
    border: 0;
    background: transparent;
    resize: none;
    font: inherit;
    font-size: 15px;
    line-height: 1.5;
    color: var(--fg);
    outline: none;
  }
  .describe-input::placeholder {
    color: var(--muted);
  }
  .describe-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-top: 6px;
  }
  .describe-help {
    font-size: 11.5px;
    color: var(--muted);
  }

  .tpl-label {
    margin: 18px 0 8px;
  }
  .templates {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
    gap: 8px;
  }
  .tpl {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    padding: 12px 12px 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    text-align: left;
    color: inherit;
    cursor: pointer;
  }
  .tpl:hover {
    border-color: var(--border2);
    background: var(--surface);
  }
  .tpl:hover .tpl-title {
    color: var(--accent);
  }
  .tpl-ico {
    color: var(--accent);
    margin-bottom: 2px;
  }
  .tpl-title {
    font-size: 13.5px;
    font-weight: 500;
    color: var(--fg-bright);
  }
  .tpl-desc {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.4;
  }
  .tpl-meta {
    font-size: 11px;
    color: var(--muted);
    margin-top: 4px;
  }
  .nr-foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 14px;
  }

  .runner-form {
    max-width: 720px;
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
    align-items: center;
    gap: 6px;
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
  :global(.spin) {
    animation: runner-spin 0.8s linear infinite;
  }
  @keyframes runner-spin {
    to {
      transform: rotate(360deg);
    }
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
    .new-runner,
    .new-runner.first {
      padding: 16px;
      margin-top: 0;
    }
  }
</style>
