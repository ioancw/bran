<script lang="ts">
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import Icon from '../components/Icon.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import type { ProjectSummary } from '../lib/types'

  let projects = $state<ProjectSummary[]>([])
  let loaded = $state(false) // distinguish "still loading" from "genuinely empty"
  let error = $state<string | null>(null)

  let showForm = $state(false)
  let newName = $state('')
  let newDesc = $state('')
  let newDaily = $state(false) // keep a daily auto-written summary
  let creating = $state(false)
  let formError = $state<string | null>(null)

  async function load() {
    error = null
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

  async function create(e?: Event) {
    e?.preventDefault()
    if (creating) return
    if (!newName.trim()) {
      formError = 'Give the project a name.'
      return
    }
    formError = null
    creating = true
    let p
    try {
      p = await api.newProject(newName.trim(), newDesc.trim(), newDaily)
    } catch (err) {
      formError = errorText(err)
      return
    } finally {
      creating = false
    }
    newName = ''
    newDesc = ''
    newDaily = false
    showForm = false
    navigate('/projects/' + p.id) // drop into the new workspace
  }
</script>

<Page title="Projects">
  {#snippet subtitle()}{loaded
      ? `${projects.length} ${projects.length === 1 ? 'workspace' : 'workspaces'}`
      : 'Loading…'}{/snippet}
  {#snippet actions()}
    <button class="btn-primary" onclick={() => (showForm = !showForm)}
      ><Icon name="plus" size={14} stroke={2.2} />New project</button
    >
  {/snippet}

  <div class="space-y-4">
    {#if error}<ErrorBanner {error} onretry={load} />{/if}

    {#if showForm}
      <form class="card-quiet form" onsubmit={create}>
        <h2 class="form-title">New project</h2>
        <input class="field" bind:value={newName} placeholder="Name" aria-label="Project name" />
        <input
          class="field"
          bind:value={newDesc}
          placeholder="Description (optional)"
          aria-label="Project description"
        />
        <label class="daily-check">
          <input type="checkbox" bind:checked={newDaily} />
          <span>
            <strong>Keep a daily summary</strong>
            <span class="daily-help"
              >Each evening the summariser reads the project's chats and runs and rewrites a short brief, so
              every new chat starts informed. One small run a day.</span
            >
          </span>
        </label>
        {#if formError}<div role="alert" class="form-error">{formError}</div>{/if}
        <div class="form-actions">
          <button
            type="button"
            class="btn-ghost"
            onclick={() => {
              showForm = false
              formError = null
            }}>Cancel</button
          >
          <button type="submit" class="btn-primary" disabled={creating}
            >{creating ? 'Creating…' : 'Create project'}</button
          >
        </div>
      </form>
    {/if}

    {#if !loaded}
      <Skeleton rows={4} />
    {:else if !projects.length && !showForm}
      <!-- First-run: a project is the front door, so explain it rather than
           showing an empty grid. -->
      <EmptyState
        title="No projects yet"
        hint="A project keeps a topic's chats, instructions, files and scheduled runners in one place. Your projects will appear here."
        icon="folder"
      >
        <button class="btn-primary" onclick={() => (showForm = true)}
          ><Icon name="plus" size={14} stroke={2.2} />Create your first project</button
        >
      </EmptyState>
    {:else}
      <p class="intro">
        A project keeps a topic's chats, instructions, files and scheduled runners together.
      </p>
      <div class="proj-grid">
        {#each projects as p (p.id)}
          <a href={href('/projects/' + encodeURIComponent(p.id))} use:link class="card project-card">
            <div class="proj-head">
              <Icon name="folder" size={16} class="proj-ico" />
              <span class="proj-name">{p.name}</span>
              <span class="proj-count">{p.n_chats} {p.n_chats === 1 ? 'chat' : 'chats'}</span>
            </div>
            {#if p.description}<p class="proj-desc">{p.description}</p>{/if}
          </a>
        {/each}
      </div>
    {/if}
  </div>
</Page>

<style>
  .daily-check {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: 13px;
    color: var(--fg-dim);
    cursor: pointer;
  }
  .daily-check input {
    cursor: pointer;
    margin-top: 2px;
  }
  .daily-check strong {
    display: block;
    color: var(--fg-bright);
    font-weight: 500;
  }
  .daily-help {
    display: block;
    font-size: 12px;
    color: var(--muted);
    line-height: 1.4;
  }
  .form {
    max-width: 480px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .form-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
    margin-bottom: 2px;
  }
  .form-error {
    color: var(--red);
    font-size: 12px;
  }
  .form-actions {
    display: flex;
    gap: 6px;
    justify-content: flex-end;
    margin-top: 4px;
  }
  .intro {
    font-size: 13px;
    color: var(--muted);
    margin: 0 0 4px;
  }
  .proj-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
  }
  .project-card {
    display: block;
    text-decoration: none;
  }
  .project-card:hover {
    text-decoration: none;
  }
  .proj-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  :global(.proj-ico) {
    color: var(--muted);
  }
  .proj-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .proj-count {
    margin-left: auto;
    flex-shrink: 0;
    font-size: 12px;
    color: var(--muted);
  }
  .proj-desc {
    font-size: 13px;
    color: var(--fg-dim);
    margin: 8px 0 0;
    line-height: 1.5;
  }
</style>
