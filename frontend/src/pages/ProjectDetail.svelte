<script lang="ts">
  // Project home, modelled on Cowork: a launcher and the project's
  // conversations (as cards) in the center, the project's settings/context as a
  // single narrow rail on the right.
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { setScope } from '../lib/workspace.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { errorText } from '../lib/errors'
  import { toast } from '../lib/toast.svelte'
  import { relativeTime } from '../lib/time'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Icon from '../components/Icon.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import Composer from '../components/Composer.svelte'
  import ProjectRail from '../components/ProjectRail.svelte'
  import type { Catalog, ProjectDetail } from '../lib/types'

  let { projectId }: { projectId: string } = $props()

  let data = $state<ProjectDetail | null>(null)
  let error = $state<string | null>(null)
  let name = $state('')
  let prompt = $state('')
  let catalog = $state<Catalog>({ agents: [], commands: [] })

  async function load() {
    error = null
    try {
      data = await api.projectDetail(projectId)
      name = data.project.name
    } catch (e) {
      error = String(e)
    }
  }
  $effect(() => {
    void projectId
    setScope(projectId) // sidebar Recents shows this project's conversations
    void load()
  })
  $effect(() => {
    void api
      .catalog()
      .then((c) => (catalog = c))
      .catch(() => {})
  })

  function launch() {
    const t = prompt.trim()
    const q = t ? '&prompt=' + encodeURIComponent(t) : ''
    navigate('/chat?project=' + encodeURIComponent(projectId) + q)
  }
  function chatHref(id: string): string {
    return href('/chat/' + encodeURIComponent(id) + '?project=' + encodeURIComponent(projectId))
  }
  async function deleteProject() {
    if (
      !(await confirmDialog(
        `Delete project "${name}"? Its chats are kept and move to Recents; its runners are detached. Nothing else is lost.`,
        'Delete project',
      ))
    )
      return
    try {
      await api.deleteProject(projectId)
    } catch (e) {
      toast(errorText(e), 'err') // action failure → toast, not the load banner
      return
    }
    setScope(null)
    navigate('/projects')
  }
</script>

<Page title={name || 'Project'}>
  {#snippet subtitle()}Project{/snippet}
  {#snippet actions()}
    <button class="btn-outline" onclick={deleteProject}><Icon name="trash" size={14} />Delete</button>
  {/snippet}

  {#if error}
    <ErrorBanner {error} onretry={load} />
  {:else if !data}
    <Skeleton rows={5} />
  {:else}
    <div class="ph-grid">
      <!-- Center: launcher + conversations (takes the remaining width) -->
      <div class="space-y-6">
        <Composer
          bind:value={prompt}
          {catalog}
          hint="Enter to start, Shift+Enter for a new line"
          placeholder={`What would you like to work on in ${name}?`}
          rows={3}
          onsubmit={launch}
        />

        <section>
          <div class="label-cap" style="margin-bottom: 8px;">Recent chats · {data.chats.length}</div>
          {#if data.chats.length}
            <div class="space-y-2">
              {#each data.chats as c (c.id)}
                <a href={chatHref(c.id)} use:link class="conv-card card">
                  <span class="conv-icon" aria-hidden="true"><Icon name="message-square" size={15} /></span>
                  <div style="flex: 1; min-width: 0;">
                    <div class="conv-title">{c.title}</div>
                    <div class="conv-meta">{c.agent}</div>
                  </div>
                  <span class="conv-time">{relativeTime(c.updated_at)}</span>
                </a>
              {/each}
            </div>
          {:else}
            <p class="quiet">No chats in this project yet. Start one above.</p>
          {/if}
        </section>
      </div>

      <!-- Right: settings/context rail (fixed width) -->
      <aside>
        <ProjectRail {projectId} mode="home" onrenamed={(n) => (name = n)} />
      </aside>
    </div>
  {/if}
</Page>

<style>
  /* Chats fill the remaining width; settings sit in a fixed rail on the right. */
  .ph-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 380px;
    gap: 24px;
    align-items: start;
  }
  @media (max-width: 900px) {
    .ph-grid {
      grid-template-columns: 1fr;
    }
  }
  .quiet {
    font-size: 13px;
    color: var(--muted);
  }
  .conv-card {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    text-decoration: none;
  }
  .conv-card:hover {
    text-decoration: none;
  }
  .conv-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    flex-shrink: 0;
    border-radius: 8px;
    background: var(--surface2);
    color: var(--muted);
  }
  .conv-title {
    font-size: 14px;
    font-weight: 500;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .conv-meta {
    font-size: 12px;
    color: var(--muted);
    margin-top: 1px;
  }
  .conv-time {
    flex-shrink: 0;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
</style>
