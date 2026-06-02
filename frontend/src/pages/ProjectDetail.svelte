<script lang="ts">
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { relativeTime, fmtCost, localDateTime, shortId } from '../lib/time'
  import StatusBadge from '../components/StatusBadge.svelte'
  import type { AgentInfo, ProjectDetail } from '../lib/types'

  let { projectId }: { projectId: string } = $props()

  let data = $state<ProjectDetail | null>(null)
  let agents = $state<AgentInfo[]>([])
  let error = $state<string | null>(null)

  // Memory editor
  let instructions = $state('')
  let name = $state('')
  let description = $state('')
  let savedFlash = $state(false)

  // New-schedule form
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
    } catch (e) {
      error = String(e)
    }
  }
  $effect(() => {
    void projectId
    void load()
  })
  $effect(() => {
    void api.agents().then((a) => (agents = a)).catch(() => {})
  })

  const isInbox = $derived(data?.project.is_inbox ?? false)

  async function saveMemory() {
    await api.saveProject(projectId, { name, description, instructions })
    savedFlash = true
    setTimeout(() => (savedFlash = false), 2200)
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
    if (!confirm(`Delete schedule "${n}"?`)) return
    await api.deleteSchedule(n)
    await load()
  }
  async function deleteProject() {
    if (!confirm(`Delete project "${name}"? Its chats move to Inbox; nothing is lost.`)) return
    await api.deleteProject(projectId)
    navigate('/projects')
  }
  function newChatHere() {
    navigate('/chat?project=' + encodeURIComponent(projectId))
  }
</script>

<header class="page-header">
  <h1>{name || 'Project'}</h1>
  <span class="subheading">workspace</span>
  <div class="page-actions">
    <button class="btn-primary" onclick={newChatHere}>+ new chat</button>
    {#if !isInbox}<button class="btn-outline" onclick={deleteProject}>delete</button>{/if}
  </div>
</header>

<div class="px-8 py-6">
  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}
  {#if data}
    <div class="grid grid-cols-3 gap-6">
      <!-- Left: memory + meta -->
      <aside class="col-span-1 space-y-3">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 6px;">Project memory</div>
          <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">
            Appended to every chat's system prompt in this project.
          </p>
          <input class="field" bind:value={name} placeholder="name" style="margin-bottom: 6px;" />
          <input class="field" bind:value={description} placeholder="description" style="margin-bottom: 6px;" />
          <textarea class="field" bind:value={instructions} rows="10"
                    placeholder="Always-on instructions / facts for this project…"
                    style="resize: vertical; font-family: var(--font-mono); font-size: 12px;"></textarea>
          <div style="display: flex; align-items: center; gap: 10px; margin-top: 8px;">
            <button class="btn-primary" onclick={saveMemory}>save</button>
            {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);">saved ✓</span>{/if}
          </div>
        </div>
      </aside>

      <!-- Right: the workspace activity -->
      <div class="col-span-2 space-y-6">
        <!-- Chats -->
        <section>
          <div class="label-cap" style="margin-bottom: 8px;">Conversations ({data.chats.length})</div>
          {#if !data.chats.length}
            <div class="text-muted" style="font-size: 13px; font-style: italic;">No chats yet — start one above.</div>
          {:else}
            <div class="space-y-2">
              {#each data.chats as c}
                <a href={href('/chat/' + encodeURIComponent(c.id))} use:link class="card" style="display: flex; gap: 10px; align-items: baseline; text-decoration: none;">
                  <span class="text-bright" style="font-weight: 500;">{c.title}</span>
                  <span class="mono text-accent-soft" style="font-size: 11px;">{c.agent}</span>
                  <span class="ml-auto label-cap">{relativeTime(c.updated_at)}</span>
                </a>
              {/each}
            </div>
          {/if}
        </section>

        <!-- Schedules -->
        <section>
          <div style="display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px;">
            <span class="label-cap">Schedules ({data.schedules.length})</span>
            <button class="btn-ghost ml-auto" onclick={() => (showSchedForm = !showSchedForm)}>+ add</button>
          </div>
          {#if showSchedForm}
            <div class="card-quiet" style="margin-bottom: 8px;">
              <div class="grid grid-cols-2 gap-4">
                <input class="field" bind:value={sName} placeholder="name (unique)" />
                <select class="field" bind:value={sAgent}>
                  {#each agents as a}<option value={a.name}>{a.name}</option>{/each}
                </select>
                <input class="field" bind:value={sCron} placeholder="cron e.g. 0 8 * * *" />
                <input class="field" bind:value={sTask} placeholder="task / prompt" />
              </div>
              <div style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px;">
                <button class="btn-ghost" onclick={() => (showSchedForm = false)}>cancel</button>
                <button class="btn-primary" onclick={addSchedule}>add schedule</button>
              </div>
            </div>
          {/if}
          {#if data.schedules.length}
            <div class="space-y-2">
              {#each data.schedules as s}
                <div class="card-quiet" style="display: flex; gap: 10px; align-items: baseline;">
                  <span class="text-bright">{s.name}</span>
                  <span class="mono text-accent-soft" style="font-size: 11px;">{s.agent}</span>
                  <span class="mono text-dim" style="font-size: 11px;">{s.cron}</span>
                  <button class="btn-ghost ml-auto" onclick={() => removeSchedule(s.name)}>×</button>
                </div>
              {/each}
            </div>
          {/if}
        </section>

        <!-- Activity (runs) -->
        <section>
          <div class="label-cap" style="margin-bottom: 8px;">Activity ({data.runs.length})</div>
          {#if !data.runs.length}
            <div class="text-muted" style="font-size: 13px; font-style: italic;">No runs in this project yet.</div>
          {:else}
            <div class="space-y-2">
              {#each data.runs.slice(0, 20) as r}
                <a href={href('/runs/' + r.id)} use:link class="card-quiet" style="display: flex; gap: 10px; align-items: baseline; text-decoration: none;">
                  <span class="mono text-dim" style="font-size: 11px;">{shortId(r.id)}</span>
                  <span class="text-bright">{r.agent}</span>
                  <StatusBadge status={r.status} />
                  <span class="ml-auto text-dim" style="font-size: 11px;">{localDateTime(r.started_at)} · {fmtCost(r.total_cost_usd)}</span>
                </a>
              {/each}
            </div>
          {/if}
        </section>
      </div>
    </div>
  {/if}
</div>
