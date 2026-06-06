<script lang="ts">
  // The project-context right rail (Cowork-style), shared by the project hub
  // and the Chat page so context lives *beside* the conversation. Self-loads
  // the project's detail and owns its own edits (instructions, memory,
  // scheduled runners). Pass a projectId; it does the rest.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { workspace, loadProjects } from '../lib/workspace.svelte'
  import { relativeTime, fmtCost } from '../lib/time'
  import { confirmDialog } from '../lib/confirm.svelte'
  import Section from './Section.svelte'
  import StatusBadge from './StatusBadge.svelte'
  import type { AgentInfo, ProjectDetail } from '../lib/types'

  // `home` = the project page (configure: editable Knowledge + Scheduled open,
  // no live Progress). `chat` = beside a conversation (work: Progress on top,
  // config collapsed to reference).
  let { projectId, onrenamed, mode = 'home' }: {
    projectId: string
    onrenamed?: (name: string) => void
    mode?: 'home' | 'chat'
  } = $props()

  let data = $state<ProjectDetail | null>(null)
  let agents = $state<AgentInfo[]>([])

  let name = $state('')
  let description = $state('')
  let instructions = $state('')
  let savedFlash = $state(false)
  let newMemory = $state('')

  let showSchedForm = $state(false)
  let sName = $state('')
  let sAgent = $state('orchestrator')
  let sCron = $state('0 8 * * *')
  let sTask = $state('')

  async function load() {
    try {
      data = await api.projectDetail(projectId)
      name = data.project.name
      description = data.project.description
      instructions = data.project.instructions
    } catch {
      /* ignore — rail just stays empty */
    }
  }
  $effect(() => {
    void projectId
    void workspace.activityTick // reload Progress when a chat/run completes
    void load()
  })
  $effect(() => {
    void api.agents().then((a) => (agents = a)).catch(() => {})
  })

  async function save() {
    await api.saveProject(projectId, { name, description, instructions })
    savedFlash = true
    onrenamed?.(name)
    void loadProjects() // keep names fresh elsewhere (sidebar, breadcrumb)
    setTimeout(() => (savedFlash = false), 2200)
  }
  async function addMem() {
    const t = newMemory.trim()
    if (!t) return
    await api.addMemory(projectId, t)
    newMemory = ''
    await load()
  }
  async function removeMem(id: string) {
    await api.deleteMemory(projectId, id)
    await load()
  }
  async function addSchedule() {
    if (!sName.trim() || !sCron.trim()) return
    await api.newSchedule({ name: sName.trim(), agent: sAgent, cron: sCron.trim(), task: sTask, project_id: projectId })
    sName = ''
    sTask = ''
    showSchedForm = false
    await load()
  }
  async function removeSchedule(n: string) {
    if (!(await confirmDialog(`Delete runner "${n}"?`))) return
    await api.deleteSchedule(n)
    await load()
  }
</script>

{#if data}
  <div class="space-y-4">
    <!-- Progress is the chat-side "what's happening now"; the home page is for
         setup, so it leads with editable config instead. -->
    {#if mode === 'chat'}
      <Section label="Progress">
        <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">Background runs in this project — chats, spawned agents, and scheduled runners.</p>
        {#if workspace.streaming}
          <div class="prog-live">
            <span class="live-dot sm"></span>
            <span class="text-bright" style="font-size: 12px;">{workspace.liveLabel || 'working…'}</span>
          </div>
        {/if}
        {#if data.runs.length}
          <div class="space-y-2">
            {#each data.runs.slice(0, 8) as r}
              <a href={href('/runs/' + r.id)} use:link class="prog-row" style="text-decoration: none;">
                <span class="text-bright" style="font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.agent}</span>
                <StatusBadge status={r.status} />
                <span class="text-muted" style="font-size: 10px; white-space: nowrap;">{relativeTime(r.started_at)}{#if r.total_cost_usd} · {fmtCost(r.total_cost_usd)}{/if}</span>
              </a>
            {/each}
          </div>
        {:else if !workspace.streaming}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">no activity yet</div>
        {/if}
      </Section>
    {/if}

    <!-- One "Knowledge" home for everything the agent knows: the always-on
         instructions (prose) and the discrete pinned facts. Expanded on the
         project page (configure), collapsed beside a chat (reference). -->
    <Section label="Knowledge" open={mode === 'home'}>
      {#if mode === 'home'}
        <!-- Project page: full editable config. -->
        <p class="text-muted" style="font-size: 11px; margin-bottom: 10px;">What the agent always knows here — applied to every chat & attached runner.</p>

        <div class="label-cap" style="margin-bottom: 6px;">Instructions · always on</div>
        <textarea class="field" bind:value={instructions} rows="7"
                  placeholder="How the agent should behave in this project…"
                  style="resize: vertical; font-family: var(--font-mono); font-size: 12px;"></textarea>
        <div style="display: flex; align-items: center; gap: 10px; margin-top: 8px;">
          <button class="btn-primary" onclick={save}>save</button>
          {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);">saved ✓</span>{/if}
        </div>

        <div class="rail-divider"></div>

        <div class="label-cap" style="margin-bottom: 6px;">Pinned facts</div>
        <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">Discrete facts — added here, via "remember that…", or /remember.</p>
        {#if data.memories.length}
          <div class="space-y-2" style="margin-bottom: 8px;">
            {#each data.memories as m}
              <div style="display: flex; gap: 8px; align-items: flex-start; font-size: 12px;">
                <span class="text-fg" style="flex: 1; line-height: 1.4;">{m.text}</span>
                <button class="btn-ghost" onclick={() => removeMem(m.id)} title="forget">×</button>
              </div>
            {/each}
          </div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic; margin-bottom: 8px;">none yet</div>
        {/if}
        <div style="display: flex; gap: 6px;">
          <input class="field" bind:value={newMemory} placeholder="pin a fact…"
                 onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addMem() } }} />
          <button class="btn-primary" onclick={addMem}>pin</button>
        </div>
      {:else}
        <!-- Beside a chat: read-only reference. Edit on the project page. -->
        <div class="label-cap" style="margin-bottom: 6px;">Instructions · always on</div>
        {#if instructions.trim()}
          <div class="ref-text">{instructions}</div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">none set</div>
        {/if}

        <div class="label-cap" style="margin: 12px 0 6px;">Pinned facts</div>
        {#if data.memories.length}
          <div class="space-y-1">
            {#each data.memories as m}
              <div class="text-fg" style="font-size: 12px; line-height: 1.4;">• {m.text}</div>
            {/each}
          </div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">none yet</div>
        {/if}

        <a href={href('/projects/' + encodeURIComponent(projectId))} use:link class="ref-edit">edit in project →</a>
      {/if}
    </Section>

    {#if mode === 'home'}
      <Section label="Scheduled" open={true}>
        {#snippet action()}
          <button class="btn-ghost" onclick={() => (showSchedForm = !showSchedForm)}>+ add</button>
        {/snippet}
        <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">Recurring runners for this project.</p>
        {#if showSchedForm}
          <div class="card-quiet" style="margin-bottom: 8px;">
            <input class="field" bind:value={sName} placeholder="name (unique)" style="margin-bottom: 6px;" />
            <select class="field" bind:value={sAgent} style="margin-bottom: 6px;">
              {#each agents as a}<option value={a.name}>{a.name}</option>{/each}
            </select>
            <input class="field" bind:value={sCron} placeholder="cron e.g. 0 8 * * *" style="margin-bottom: 6px;" />
            <input class="field" bind:value={sTask} placeholder="task / prompt" />
            <div style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px;">
              <button class="btn-ghost" onclick={() => (showSchedForm = false)}>cancel</button>
              <button class="btn-primary" onclick={addSchedule}>add</button>
            </div>
          </div>
        {/if}
        {#if data.schedules.length}
          <div class="space-y-2">
            {#each data.schedules as s}
              <div style="display: flex; gap: 8px; align-items: baseline; font-size: 12px;">
                <span class="text-bright">{s.name}</span>
                <span class="mono text-dim">{s.cron}</span>
                <button class="btn-ghost ml-auto" onclick={() => removeSchedule(s.name)}>×</button>
              </div>
            {/each}
          </div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">none yet</div>
        {/if}
      </Section>
    {:else if data.schedules.length}
      <!-- Beside a chat: read-only list of runners. -->
      <Section label="Scheduled" open={false}>
        <div class="space-y-2">
          {#each data.schedules as s}
            <div style="display: flex; gap: 8px; align-items: baseline; font-size: 12px;">
              <span class="text-bright">{s.name}</span>
              <span class="mono text-dim">{s.cron}</span>
            </div>
          {/each}
        </div>
      </Section>
    {/if}

    <Section label="Context" open={false}>
      <div class="text-muted" style="font-size: 12px;">
        <div style="margin-bottom: 4px;">On your computer</div>
        <div class="card-quiet" style="font-size: 11px;">Local files — coming soon</div>
      </div>
    </Section>

    {#if mode === 'home'}
    <Section label="Settings" open={false}>
      <div class="label-cap" style="margin-bottom: 6px;">Name</div>
      <input class="field" bind:value={name} placeholder="name" style="margin-bottom: 10px;" />
      <div class="label-cap" style="margin-bottom: 6px;">Description</div>
      <input class="field" bind:value={description} placeholder="description" style="margin-bottom: 10px;" />
      <div style="display: flex; align-items: center; gap: 10px;">
        <button class="btn-primary" onclick={save}>save</button>
        {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);">saved ✓</span>{/if}
      </div>
    </Section>
    {/if}
  </div>
{/if}

<style>
  /* Read-only instructions preview in the chat rail. */
  .ref-text {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.5;
    color: var(--fg);
    white-space: pre-wrap;
    max-height: 180px;
    overflow-y: auto;
  }
  .ref-edit {
    display: inline-block;
    margin-top: 12px;
    font-size: 11px;
    color: var(--accent-soft);
    text-decoration: none;
  }
  .ref-edit:hover { text-decoration: underline; }
  .rail-divider {
    height: 1px;
    background: var(--border);
    margin: 14px 0;
  }
  .prog-live {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    margin-bottom: 6px;
    border-radius: var(--radius);
    background: var(--accent-glow);
  }
  .prog-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    border-radius: var(--radius);
    transition: background 0.12s var(--transition);
  }
  .prog-row:hover { background: var(--surface2); }
</style>
