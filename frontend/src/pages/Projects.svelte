<script lang="ts">
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import type { ProjectSummary } from '../lib/types'

  let projects = $state<ProjectSummary[]>([])
  let loaded = $state(false) // distinguish "still loading" from "genuinely empty"
  let error = $state<string | null>(null)

  let showForm = $state(false)
  let newName = $state('')
  let newDesc = $state('')

  async function load() {
    try {
      projects = await api.projects()
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
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

<Page title="Projects">
  {#snippet subtitle()}{projects.length} workspaces{/snippet}
  {#snippet actions()}
    <button class="btn-primary" onclick={() => (showForm = !showForm)}>+ new project</button>
  {/snippet}

  <div class="space-y-4">
    {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}

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

    {#if loaded && !projects.length && !showForm}
      <!-- First-run: a project is the front door, so explain it rather than
           showing an empty grid. -->
      <div class="empty-state">
        <div class="brackets">[       ]</div>
        <h3>no projects yet</h3>
        <p class="cta">a project is a workspace — chat, memory, and scheduled runners in one place</p>
        <div style="margin-top: 18px;">
          <button class="btn-primary" onclick={() => (showForm = true)}>+ create your first project</button>
        </div>
      </div>
    {:else}
      <section class="proj-hero">
        <div class="proj-rule"></div>
        <p class="proj-standfirst">
          A workspace keeps a project's <em>conversations, memory, and scheduled agents</em> in one place — your room to think with the fleet.
        </p>
      </section>
      <div class="label-cap" style="margin-bottom: 12px;">Workspaces · {projects.length}</div>
      <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px;">
        {#each projects as p}
          <a href={href('/projects/' + encodeURIComponent(p.id))} use:link class="card project-card" style="text-decoration: none; display: block;">
            <div style="display: flex; align-items: baseline; gap: 8px;">
              <span class="text-bright" style="font-weight: 600;">{p.name}</span>
              <span class="ml-auto label-cap">{p.n_chats} chats</span>
            </div>
            {#if p.description}<p class="text-dim" style="font-size: 13px; margin-top: 6px;">{p.description}</p>{/if}
          </a>
        {/each}
      </div>
    {/if}
  </div>
</Page>

<style>
  /* Editorial entrance: an accent rule + a serif-italic standfirst that gives
     the front door an authored, magazine feel before the workspace grid. */
  .proj-hero {
    max-width: 640px;
    margin: 4px 0 30px;
  }
  .proj-rule {
    width: 52px;
    height: 2px;
    background: var(--accent);
    margin-bottom: 18px;
  }
  .proj-standfirst {
    font-family: var(--font-prose);
    font-style: italic;
    font-weight: 400;
    font-size: 23px;
    line-height: 1.45;
    letter-spacing: -0.01em;
    color: var(--fg-dim);
    margin: 0;
  }
  .proj-standfirst em {
    font-style: italic;
    color: var(--fg-bright);
  }

  /* Subtle lift on the project cards — the grid is the front door. */
  .project-card {
    transition: border-color 0.15s var(--transition), transform 0.15s var(--transition);
  }
  .project-card:hover {
    transform: translateY(-1px);
    border-color: var(--accent-soft);
  }
</style>
