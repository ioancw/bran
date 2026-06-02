<script lang="ts">
  // Project workspace hub, modelled on Cowork: a launcher + the project's
  // conversations (center), and the project's config on the right
  // (Instructions / Scheduled / Context).
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { relativeTime, fmtCost, localDateTime, shortId } from '../lib/time'
  import StatusBadge from '../components/StatusBadge.svelte'
  import type { AgentInfo, ProjectDetail } from '../lib/types'

  let { projectId }: { projectId: string } = $props()

  let data = $state<ProjectDetail | null>(null)
  let agents = $state<AgentInfo[]>([])
  let error = $state<string | null>(null)

  let prompt = $state('')

  // Instructions editor
  let name = $state('')
  let description = $state('')
  let instructions = $state('')
  let savedFlash = $state(false)

  // New-schedule (runner) form
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

  function launch() {
    const t = prompt.trim()
    const q = t ? '&prompt=' + encodeURIComponent(t) : ''
    navigate('/chat?project=' + encodeURIComponent(projectId) + q)
  }
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
    if (!confirm(`Delete runner "${n}"?`)) return
    await api.deleteSchedule(n)
    await load()
  }
  async function deleteProject() {
    if (!confirm(`Delete project "${name}"? Its chats move to Inbox; nothing is lost.`)) return
    await api.deleteProject(projectId)
    navigate('/')
  }
</script>

<header class="page-header">
  <h1>{name || 'Project'}</h1>
  <span class="subheading">workspace</span>
  <div class="page-actions">
    {#if !isInbox}<button class="btn-outline" onclick={deleteProject}>delete</button>{/if}
  </div>
</header>

<div class="px-8 py-6">
  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}
  {#if data}
    <div class="grid grid-cols-3 gap-6">
      <!-- Center: launcher + recents + activity -->
      <div class="col-span-2 space-y-6">
        <!-- Launcher -->
        <div class="card">
          <textarea class="field" bind:value={prompt} rows="3"
                    placeholder={`What would you like to work on in ${name}?`}
                    onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); launch() } }}
                    style="resize: none; font-family: var(--font-prose); font-size: 15px;"></textarea>
          <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
            <button class="btn-primary" onclick={launch}>start →</button>
          </div>
        </div>

        <!-- Recents (this project's conversations) -->
        <section>
          <div class="label-cap" style="margin-bottom: 8px;">Recents ({data.chats.length})</div>
          {#if !data.chats.length}
            <div class="text-muted" style="font-size: 13px; font-style: italic;">No conversations yet — start one above.</div>
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

        <!-- Activity (recent runs in this project) -->
        {#if data.runs.length}
          <section>
            <div class="label-cap" style="margin-bottom: 8px;">Activity ({data.runs.length})</div>
            <div class="space-y-2">
              {#each data.runs.slice(0, 12) as r}
                <a href={href('/runs/' + r.id)} use:link class="card-quiet" style="display: flex; gap: 10px; align-items: baseline; text-decoration: none;">
                  <span class="mono text-dim" style="font-size: 11px;">{shortId(r.id)}</span>
                  <span class="text-bright">{r.agent}</span>
                  <StatusBadge status={r.status} />
                  <span class="ml-auto text-dim" style="font-size: 11px;">{localDateTime(r.started_at)} · {fmtCost(r.total_cost_usd)}</span>
                </a>
              {/each}
            </div>
          </section>
        {/if}
      </div>

      <!-- Right rail: Instructions / Scheduled / Context -->
      <aside class="col-span-1 space-y-4">
        <!-- Instructions (project memory) -->
        <div class="card">
          <div class="label-cap" style="margin-bottom: 6px;">Instructions</div>
          <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">Appended to every chat & attached runner in this project.</p>
          <input class="field" bind:value={name} placeholder="name" style="margin-bottom: 6px;" />
          <input class="field" bind:value={description} placeholder="description" style="margin-bottom: 6px;" />
          <textarea class="field" bind:value={instructions} rows="8"
                    placeholder="Always-on instructions / facts for this project…"
                    style="resize: vertical; font-family: var(--font-mono); font-size: 12px;"></textarea>
          <div style="display: flex; align-items: center; gap: 10px; margin-top: 8px;">
            <button class="btn-primary" onclick={saveMemory}>save</button>
            {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);">saved ✓</span>{/if}
          </div>
        </div>

        <!-- Scheduled (runners attached to this project) -->
        <div class="card">
          <div style="display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px;">
            <span class="label-cap">Scheduled</span>
            <button class="btn-ghost ml-auto" onclick={() => (showSchedForm = !showSchedForm)}>+ add</button>
          </div>
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
        </div>

        <!-- Context (local files / memory) — stub for now -->
        <div class="card">
          <div class="label-cap" style="margin-bottom: 6px;">Context</div>
          <div class="text-muted" style="font-size: 12px;">
            <div style="margin-bottom: 4px;">On your computer</div>
            <div class="card-quiet" style="font-size: 11px;">Local files — coming soon</div>
          </div>
        </div>
      </aside>
    </div>
  {/if}
</div>
