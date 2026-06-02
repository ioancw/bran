<script lang="ts">
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import type { ProjectSummary } from '../lib/types'

  let projects = $state<ProjectSummary[]>([])
  let error = $state<string | null>(null)

  let showForm = $state(false)
  let newName = $state('')
  let newDesc = $state('')

  async function load() {
    try {
      projects = await api.projects()
    } catch (e) {
      error = String(e)
    }
  }
  $effect(() => {
    void load()
  })

  async function create() {
    if (!newName.trim()) return
    const p = await api.newProject(newName.trim(), newDesc.trim())
    newName = ''
    newDesc = ''
    showForm = false
    navigate('/projects/' + p.id) // drop into the new workspace
  }
</script>

<header class="page-header">
  <h1>Projects</h1>
  <span class="subheading">{projects.length} workspaces</span>
  <div class="page-actions">
    <button class="btn-primary" onclick={() => (showForm = !showForm)}>+ new project</button>
  </div>
</header>
<div class="px-8 py-6 space-y-4">
  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}

  {#if showForm}
    <div class="card-quiet" style="max-width: 480px;">
      <span class="label-cap" style="display: block; margin-bottom: 6px;">New project</span>
      <input class="field" bind:value={newName} placeholder="name" style="margin-bottom: 6px;" />
      <input class="field" bind:value={newDesc} placeholder="description (optional)" />
      <div style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px;">
        <button class="btn-ghost" onclick={() => (showForm = false)}>cancel</button>
        <button class="btn-primary" onclick={create}>create</button>
      </div>
    </div>
  {/if}

  <div class="grid grid-cols-3 gap-4">
    {#each projects as p}
      <a href={href('/projects/' + encodeURIComponent(p.id))} use:link class="card" style="text-decoration: none; display: block;">
        <div style="display: flex; align-items: baseline; gap: 8px;">
          <span class="text-bright" style="font-weight: 600;">{p.name}</span>
          {#if p.is_inbox}<span class="label-cap">inbox</span>{/if}
          <span class="ml-auto label-cap">{p.n_chats} chats</span>
        </div>
        {#if p.description}<p class="text-dim" style="font-size: 13px; margin-top: 6px;">{p.description}</p>{/if}
      </a>
    {/each}
  </div>
</div>
