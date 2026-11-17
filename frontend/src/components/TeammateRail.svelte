<script lang="ts">
  // Beside a teammate's chat: who it is, its standing brief, the automations
  // that do its repeating work, and what they last delivered.
  import { api } from '../lib/api'
  import { errorText } from '../lib/errors'
  import { href, link, navigate } from '../lib/router.svelte'
  import { describeCron } from '../lib/schedule'
  import { countdown, localDateTime, relativeTime } from '../lib/time'
  import { teammateHint } from '../lib/teammates'
  import { toast } from '../lib/toast.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { bumpActivity, workspace } from '../lib/workspace.svelte'
  import { onReturn } from '../lib/refresh'
  import Icon from './Icon.svelte'
  import Section from './Section.svelte'
  import type { AgentInfo, ScheduleRecord, TeammateDetail } from '../lib/types'

  let {
    teammateId,
    onchange,
  }: {
    teammateId: string
    // The teammate was renamed or re-briefed: the chat header follows.
    onchange?: (t: TeammateDetail) => void
  } = $props()

  let mate = $state<TeammateDetail | null>(null)
  let agents = $state<AgentInfo[]>([])
  let spare = $state<ScheduleRecord[]>([]) // automations no teammate has
  let error = $state('')

  let editing = $state(false)
  let name = $state('')
  let agent = $state('')
  let brief = $state('')
  let saving = $state(false)

  let seq = 0
  async function load() {
    const mine = ++seq
    const id = teammateId
    try {
      const [t, all, roster] = await Promise.all([api.teammate(id), api.schedules(), api.agents()])
      if (mine !== seq) return
      mate = t
      spare = all.filter((s) => !s.teammate_id)
      agents = roster
      error = ''
    } catch (e) {
      if (mine === seq) error = errorText(e)
    }
  }
  $effect(() => {
    void teammateId
    void workspace.activityTick // a turn finished, or a run landed
    void load()
  })
  $effect(() => onReturn(() => void load()))

  function startEdit() {
    if (!mate) return
    name = mate.name
    agent = mate.agent
    brief = mate.brief
    editing = true
  }
  async function save() {
    if (!mate || saving) return
    saving = true
    try {
      const next = await api.updateTeammate(mate.id, { name, agent, brief })
      mate = { ...mate, ...next }
      editing = false
      onchange?.(mate)
      bumpActivity()
    } catch (e) {
      toast(errorText(e), 'err')
    } finally {
      saving = false
    }
  }
  async function assign(schedule: string, attach: boolean) {
    if (!mate) return
    try {
      await api.teammateAutomation(mate.id, schedule, attach)
      await load()
      bumpActivity()
    } catch (e) {
      toast(errorText(e), 'err')
    }
  }
  async function remove() {
    if (!mate) return
    const ok = await confirmDialog(
      `Remove ${mate.name}? Its automations keep running as plain automations and this chat stays in Recents.`,
      'Remove teammate',
    )
    if (!ok) return
    try {
      await api.deleteTeammate(mate.id)
      bumpActivity()
      navigate('/teammates')
    } catch (e) {
      toast(errorText(e), 'err')
    }
  }
  const when = (s: ScheduleRecord) => (s.run_at ? `Once · ${localDateTime(s.run_at)}` : describeCron(s.cron))
</script>

{#if error && !mate}
  <p class="text-muted rail-note">{error}</p>
{:else if mate}
  <div class="mate-head">
    <span class="mate-dot {mate.state}"></span>
    <span class="mate-name">{mate.name}</span>
    <span class="label-cap">Teammate</span>
  </div>
  <div class="mate-sub">
    <span class="pill mute">{mate.agent}</span>
    {#if teammateHint(mate)}<span class="text-muted">{teammateHint(mate)}</span>{/if}
  </div>

  <Section label="Standing brief">
    {#snippet action()}
      {#if !editing}
        <button class="btn-icon" title="Edit the teammate" aria-label="Edit the teammate" onclick={startEdit}>
          <Icon name="pencil" size={13} />
        </button>
      {/if}
    {/snippet}
    {#if editing}
      <form
        class="mate-form"
        onsubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <label class="label-cap" for="mate-name">Name</label>
        <input id="mate-name" class="field" bind:value={name} maxlength="60" />
        <label class="label-cap" for="mate-agent">Role</label>
        <select id="mate-agent" class="field" bind:value={agent}>
          {#each agents as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
        </select>
        <label class="label-cap" for="mate-brief">Brief</label>
        <textarea id="mate-brief" class="field" rows="7" bind:value={brief}></textarea>
        <div class="mate-form-actions">
          <button type="button" class="btn-ghost btn-sm" onclick={() => (editing = false)}>Cancel</button>
          <button type="submit" class="btn-primary btn-sm" disabled={saving || !name.trim()}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    {:else if mate.brief.trim()}
      <p class="mate-brief">{mate.brief}</p>
    {:else}
      <p class="text-muted rail-note">No brief yet. Tell it what it owns, here or in the chat.</p>
    {/if}
  </Section>

  <Section label="Automations">
    {#if mate.automations.length}
      <ul class="mate-list">
        {#each mate.automations as s (s.id)}
          <li class="mate-auto">
            <div class="mate-auto-top">
              <a href={href('/runners/' + encodeURIComponent(s.name))} use:link class="mate-auto-name"
                >{s.name}</a
              >
              <button
                class="btn-icon"
                title="Take this automation back (it keeps running)"
                aria-label="Take {s.name} back from {mate.name}"
                onclick={() => assign(s.name, false)}><Icon name="x" size={12} /></button
              >
            </div>
            <div class="mate-auto-when">
              {when(s)}{#if !s.enabled}
                · paused{:else if s.next_run}
                · {countdown(s.next_run)}{/if}
            </div>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="text-muted rail-note">None: it works when you message it.</p>
    {/if}
    {#if spare.length}
      <select
        class="field mate-attach"
        aria-label="Give {mate.name} an existing automation"
        value=""
        onchange={(e) => {
          const v = e.currentTarget.value
          e.currentTarget.value = ''
          if (v) void assign(v, true)
        }}
      >
        <option value="">Give it an existing automation…</option>
        {#each spare as s (s.id)}<option value={s.name}>{s.name}</option>{/each}
      </select>
    {/if}
  </Section>

  <Section label="Delivered">
    {#if mate.deliveries.length}
      <ul class="mate-list">
        {#each mate.deliveries as d (d.id)}
          <li>
            <a href={href('/read/' + d.id)} use:link class="mate-delivery">
              <span class="mate-delivery-when">{relativeTime(d.started_at)}</span>
              <span class="mate-delivery-text">{d.excerpt}</span>
            </a>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="text-muted rail-note">Nothing yet. Scheduled work appears here as it finishes.</p>
    {/if}
  </Section>

  <button class="btn-text mate-remove" onclick={remove}>Remove teammate</button>
{/if}

<style>
  .mate-head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .mate-name {
    font-size: 15px;
    font-weight: 600;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .mate-sub {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    font-size: 12px;
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
    animation: mate-pulse 1.4s ease-in-out infinite;
  }
  .mate-dot.failed {
    background: var(--red);
  }
  .mate-dot.paused {
    background: transparent;
    box-shadow: inset 0 0 0 1.5px var(--muted);
  }
  @keyframes mate-pulse {
    50% {
      opacity: 0.35;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .mate-dot.working {
      animation: none;
    }
  }
  .mate-brief {
    margin: 0;
    font-size: 13px;
    line-height: 1.55;
    color: var(--fg);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .rail-note {
    margin: 0;
    font-size: 13px;
  }
  .mate-form {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .mate-form .label-cap {
    margin-top: 5px;
  }
  .mate-form textarea {
    resize: vertical;
    font-size: 13px;
    line-height: 1.5;
  }
  .mate-form-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
    margin-top: 6px;
  }
  .mate-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .mate-auto-top {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .mate-auto-name {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mate-auto-when {
    font-size: 12px;
    color: var(--muted);
  }
  .mate-attach {
    margin-top: 10px;
    font-size: 13px;
  }
  .mate-delivery {
    display: flex;
    flex-direction: column;
    gap: 1px;
    text-decoration: none;
  }
  .mate-delivery-when {
    font-size: 12px;
    color: var(--muted);
  }
  .mate-delivery-text {
    font-size: 13px;
    line-height: 1.45;
    color: var(--fg-dim);
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
  }
  .mate-delivery:hover .mate-delivery-text {
    color: var(--fg-bright);
  }
  .mate-remove {
    margin: 10px 2px 0;
    font-size: 12px;
    color: var(--muted);
  }
</style>
