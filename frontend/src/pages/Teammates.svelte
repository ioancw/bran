<script lang="ts">
  // Teammates: agents with a name and a standing job. Each has one chat you
  // steer it from, and automations that do its repeating work. Creating one
  // opens that chat, where setup is finished by talking to it.
  import { api } from '../lib/api'
  import { errorText } from '../lib/errors'
  import { onReturn } from '../lib/refresh'
  import { href, link, navigate, router } from '../lib/router.svelte'
  import { teammateHint, teammateHref } from '../lib/teammates'
  import { bumpActivity } from '../lib/workspace.svelte'
  import Page from '../components/Page.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import CronField from '../components/CronField.svelte'
  import Icon from '../components/Icon.svelte'
  import type { AgentInfo, ScheduleRecord, Teammate } from '../lib/types'

  let mates = $state<Teammate[]>([])
  let agents = $state<AgentInfo[]>([])
  let spare = $state<ScheduleRecord[]>([]) // automations no teammate has
  let loaded = $state(false)
  let error = $state<string | null>(null)

  // The create form: open from ?new=1, or straight away when there are none.
  let formOpen = $state(false)
  let name = $state('')
  let agent = $state('orchestrator')
  let brief = $state('')
  type Work = 'demand' | 'schedule' | 'adopt'
  let work = $state<Work>('demand')
  let schedule = $state('')
  let adopt = $state('')
  let creating = $state(false)
  let formError = $state('')

  const agentInfo = $derived(agents.find((a) => a.name === agent))
  const canCreate = $derived(
    !!name.trim() &&
      !creating &&
      (work === 'demand' ||
        (work === 'schedule' && !!schedule.trim() && !!brief.trim()) ||
        (work === 'adopt' && !!adopt)),
  )

  // Taking over an automation: the teammate inherits its agent and, unless
  // you have written one, its task as the brief.
  function onAdopt(value: string) {
    adopt = value
    const s = spare.find((x) => x.name === value)
    if (!s) return
    agent = s.agent
    if (!brief.trim()) brief = s.task
    if (!name.trim()) name = s.name
  }

  let seq = 0
  async function load() {
    const mine = ++seq
    try {
      const [t, roster, all] = await Promise.all([api.teammates(), api.agents(), api.schedules()])
      if (mine !== seq) return
      mates = t
      agents = roster
      spare = all.filter((s) => !s.teammate_id)
      if (!agents.some((a) => a.name === agent) && agents.length) agent = agents[0].name
      error = null
    } catch (e) {
      if (mine === seq) error = String(e)
    } finally {
      if (mine === seq) loaded = true
    }
  }
  $effect(() => {
    void load()
  })
  $effect(() => onReturn(() => void load()))
  $effect(() => {
    if (router.route.query.get('new')) formOpen = true
  })

  async function create() {
    if (!canCreate) return
    creating = true
    formError = ''
    try {
      const made = await api.newTeammate({
        name,
        agent,
        brief,
        schedule: work === 'schedule' ? schedule : '',
        adopt: work === 'adopt' ? adopt : '',
      })
      bumpActivity()
      // Setup finishes in its chat.
      navigate(teammateHref(made))
    } catch (e) {
      formError = errorText(e)
    } finally {
      creating = false
    }
  }
</script>

<Page title="Teammates">
  {#snippet subtitle()}Persistent agents that work for you{/snippet}
  {#snippet actions()}
    {#if loaded && mates.length && !formOpen}
      <button class="btn-primary" onclick={() => (formOpen = true)}>
        <Icon name="plus" size={14} stroke={2.2} />Create teammate
      </button>
    {/if}
  {/snippet}

  {#if error}<ErrorBanner {error} onretry={load} />{/if}
  {#if !loaded}
    <Skeleton rows={4} />
  {:else}
    {#if formOpen || !mates.length}
      <form
        class="card mate-create"
        onsubmit={(e) => {
          e.preventDefault()
          void create()
        }}
      >
        <h2 class="create-title">{mates.length ? 'New teammate' : 'Hire your first teammate'}</h2>
        <p class="create-lede">
          A teammate is an agent with a name and a standing job. It has one chat that stays open: you hand it
          work there, ask what it has done, and tell it what to change.
        </p>

        <div class="create-grid">
          <div class="create-field">
            <label class="label-cap" for="tm-name">Name or role</label>
            <input
              id="tm-name"
              class="field"
              bind:value={name}
              maxlength="60"
              placeholder="e.g. Markets desk"
              autocomplete="off"
            />
          </div>
          <div class="create-field">
            <label class="label-cap" for="tm-agent">Agent</label>
            <select id="tm-agent" class="field" bind:value={agent} disabled={work === 'adopt' && !!adopt}>
              {#each agents as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
            </select>
          </div>
        </div>
        {#if agentInfo?.description}<p class="create-hint">{agentInfo.description}</p>{/if}

        <div class="create-field">
          <label class="label-cap" for="tm-brief">The job</label>
          <textarea
            id="tm-brief"
            class="field"
            rows="4"
            bind:value={brief}
            placeholder="What it owns, what to hand back, and who it is for. e.g. Brief me on rates, FX and macro each morning; lead with what changed overnight; keep it to one page."
          ></textarea>
        </div>

        <div class="create-field">
          <span class="label-cap">When it works</span>
          <div class="seg" role="group" aria-label="When it works">
            <button
              type="button"
              class="seg-btn"
              class:on={work === 'demand'}
              aria-pressed={work === 'demand'}
              onclick={() => (work = 'demand')}>When I message it</button
            >
            <button
              type="button"
              class="seg-btn"
              class:on={work === 'schedule'}
              aria-pressed={work === 'schedule'}
              onclick={() => (work = 'schedule')}>On a schedule</button
            >
            {#if spare.length}
              <button
                type="button"
                class="seg-btn"
                class:on={work === 'adopt'}
                aria-pressed={work === 'adopt'}
                onclick={() => (work = 'adopt')}>Take over an automation</button
              >
            {/if}
          </div>
          {#if work === 'schedule'}
            <div class="create-sub">
              <CronField bind:value={schedule} />
              <p class="create-hint">
                It does the job above on this schedule. You can still message it any time.
              </p>
            </div>
          {:else if work === 'adopt'}
            <div class="create-sub">
              <select
                class="field"
                aria-label="Automation to take over"
                value={adopt}
                onchange={(e) => onAdopt(e.currentTarget.value)}
              >
                <option value="">Choose an automation…</option>
                {#each spare as s (s.id)}<option value={s.name}>{s.name} · {s.agent}</option>{/each}
              </select>
              <p class="create-hint">
                The automation keeps its schedule and task. From its next run it also reads this teammate's
                brief and what you tell it in its chat.
              </p>
            </div>
          {/if}
        </div>

        {#if formError}<p class="create-error" role="alert">{formError}</p>{/if}
        <div class="create-actions">
          {#if mates.length}
            <button type="button" class="btn-ghost" onclick={() => (formOpen = false)}>Cancel</button>
          {/if}
          <button type="submit" class="btn-primary" disabled={!canCreate}>
            {creating ? 'Creating…' : 'Create and open its chat'}
          </button>
        </div>
      </form>
    {/if}

    {#if mates.length}
      <ul class="mate-grid">
        {#each mates as t (t.id)}
          <li>
            <a href={href(teammateHref(t))} use:link class="card mate-card">
              <span class="mate-top">
                <span class="mate-dot {t.state}"></span>
                <span class="mate-name">{t.name}</span>
                <span class="mate-hint">{teammateHint(t)}</span>
              </span>
              <span class="mate-brief" class:empty={!t.brief.trim()}
                >{t.brief.trim() || 'No brief yet — open its chat to give it one.'}</span
              >
              <span class="mate-foot">
                <span class="pill mute">{t.agent}</span>
                <span
                  >{t.automations.length
                    ? `${t.automations.length} automation${t.automations.length === 1 ? '' : 's'}`
                    : 'On demand'}</span
                >
              </span>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</Page>

<style>
  .mate-create {
    max-width: 720px;
    margin-bottom: 24px;
    padding: 20px 22px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .create-title {
    margin: 0;
    font-size: 17px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .create-lede {
    margin: -6px 0 0;
    font-size: 14px;
    line-height: 1.55;
    color: var(--fg-dim);
  }
  .create-grid {
    display: grid;
    grid-template-columns: 1.4fr 1fr;
    gap: 12px;
  }
  @media (max-width: 620px) {
    .create-grid {
      grid-template-columns: 1fr;
    }
  }
  .create-field {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .create-field textarea {
    resize: vertical;
    line-height: 1.5;
  }
  .create-hint {
    margin: -6px 0 0;
    font-size: 13px;
    line-height: 1.5;
    color: var(--muted);
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
  }
  .create-sub {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 6px;
  }
  .create-sub .create-hint {
    margin: 0;
  }
  .create-error {
    margin: 0;
    font-size: 13px;
    color: var(--red);
  }
  .create-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }

  .mate-grid {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 10px;
  }
  .mate-card {
    display: flex;
    flex-direction: column;
    gap: 8px;
    height: 100%;
    padding: 14px 16px;
    color: inherit;
  }
  .mate-top {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .mate-name {
    flex: 1;
    min-width: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mate-hint {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
  .mate-brief {
    font-size: 13px;
    line-height: 1.5;
    color: var(--fg-dim);
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    overflow: hidden;
  }
  .mate-brief.empty {
    color: var(--muted);
  }
  .mate-foot {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: auto;
    font-size: 12px;
    color: var(--muted);
  }
  .mate-dot {
    width: 8px;
    height: 8px;
    flex-shrink: 0;
    border-radius: 50%;
    background: var(--green);
  }
  .mate-dot.working {
    background: var(--amber);
  }
  .mate-dot.failed {
    background: var(--red);
  }
  .mate-dot.paused {
    background: transparent;
    box-shadow: inset 0 0 0 1.5px var(--muted);
  }
</style>
