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
  import type { Catalog, ChatSummary, ProjectDetail } from '../lib/types'

  let { projectId }: { projectId: string } = $props()

  let data = $state<ProjectDetail | null>(null)
  let error = $state<string | null>(null)
  let name = $state('')
  let prompt = $state('')
  let catalog = $state<Catalog>({ agents: [], commands: [] })

  // Chats vs the archived ones (fetched on demand; the project detail only
  // carries the count so the common case stays one request).
  let view = $state<'chats' | 'archived'>('chats')
  let archived = $state<ChatSummary[]>([])
  async function showArchived() {
    view = 'archived'
    try {
      archived = await api.chats(projectId, true)
    } catch (e) {
      toast(errorText(e), 'err')
    }
  }
  async function restore(c: ChatSummary) {
    try {
      await api.archiveChat(c.id, false)
    } catch (e) {
      toast(errorText(e), 'err')
      return
    }
    toast('Restored to Recents', 'ok')
    archived = archived.filter((x) => x.id !== c.id)
    await load()
    if (!archived.length) view = 'chats'
  }

  // First-visit setup: three things that make a project useful, each a step
  // to the control that does it. Shown until the first chat exists.
  const hasContext = $derived(
    !!data &&
      (data.project.instructions.trim().length > 0 || data.files.length > 0 || data.memories.length > 0),
  )
  const hasRunner = $derived(!!data && data.schedules.length > 0)
  const setupDone = $derived((hasContext ? 1 : 0) + (hasRunner ? 1 : 0))
  function focusInstructions() {
    const el = document.getElementById('project-instructions') as HTMLTextAreaElement | null
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    el?.focus()
  }
  function focusComposer() {
    const el = document.querySelector<HTMLTextAreaElement>('.composer textarea')
    el?.focus()
  }

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

        {#if !data.chats.length}
          <section class="card setup">
            <div class="setup-head">
              <span class="label-cap">Set up this project</span>
              <span class="label-cap ml-auto">{setupDone}/3</span>
            </div>
            <div class="setup-steps">
              <button class="setup-step" class:done={hasContext} onclick={focusInstructions}>
                <span class="setup-check" class:done={hasContext}
                  >{#if hasContext}<Icon name="check" size={10} stroke={3} />{/if}</span
                >
                <span class="setup-body">
                  <span class="setup-title">Add context</span>
                  <span class="setup-desc"
                    >Instructions, pinned facts and files the agent should always have. They live in the rail
                    on the right.</span
                  >
                </span>
                <Icon name="chevron-right" size={14} class="setup-chev" />
              </button>
              <a
                href={href('/runners?project=' + encodeURIComponent(projectId))}
                use:link
                class="setup-step"
                class:done={hasRunner}
              >
                <span class="setup-check" class:done={hasRunner}
                  >{#if hasRunner}<Icon name="check" size={10} stroke={3} />{/if}</span
                >
                <span class="setup-body">
                  <span class="setup-title">Attach a runner</span>
                  <span class="setup-desc"
                    >An agent on a schedule that works inside this project: a morning brief, a weekly digest,
                    a watch that alerts you.</span
                  >
                </span>
                <Icon name="chevron-right" size={14} class="setup-chev" />
              </a>
              <button class="setup-step" onclick={focusComposer}>
                <span class="setup-check"></span>
                <span class="setup-body">
                  <span class="setup-title">Start a chat</span>
                  <span class="setup-desc">Everything you discuss here keeps the project's context.</span>
                </span>
                <Icon name="chevron-right" size={14} class="setup-chev" />
              </button>
            </div>
          </section>
        {/if}

        <section>
          <div class="list-head">
            {#if data.n_archived > 0}
              <div class="seg">
                <button class="seg-btn" class:on={view === 'chats'} onclick={() => (view = 'chats')}
                  >Chats <span class="seg-n">{data.chats.length}</span></button
                >
                <button class="seg-btn" class:on={view === 'archived'} onclick={showArchived}
                  ><Icon name="archive" size={12} />Archived
                  <span class="seg-n">{data.n_archived}</span></button
                >
              </div>
            {:else}
              <div class="label-cap">Recent chats · {data.chats.length}</div>
            {/if}
          </div>
          {#if view === 'archived'}
            {#if archived.length}
              <div class="space-y-2">
                {#each archived as c (c.id)}
                  <div class="conv-card card archived-row">
                    <a href={chatHref(c.id)} use:link class="archived-link">
                      <span class="conv-icon" aria-hidden="true"><Icon name="archive" size={15} /></span>
                      <div style="flex: 1; min-width: 0;">
                        <div class="conv-title">{c.title}</div>
                        <div class="conv-meta">
                          {c.agent} · archived {relativeTime(c.archived_at ?? c.updated_at)}
                        </div>
                      </div>
                    </a>
                    <button class="btn-outline btn-sm" onclick={() => restore(c)}
                      ><Icon name="archive-restore" size={12} />Restore</button
                    >
                  </div>
                {/each}
              </div>
            {:else}
              <p class="quiet">Nothing archived.</p>
            {/if}
          {:else if data.chats.length}
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

  /* Setup checklist: three rows, each a door to the control that does it. */
  .setup {
    padding: 14px 16px 10px;
  }
  .setup-head {
    display: flex;
    align-items: center;
    margin-bottom: 6px;
  }
  .setup-steps {
    display: flex;
    flex-direction: column;
  }
  .setup-step {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    padding: 10px 4px;
    background: transparent;
    border: 0;
    border-top: 1px solid var(--border);
    border-radius: 0;
    text-align: left;
    color: inherit;
    cursor: pointer;
    text-decoration: none;
  }
  .setup-step:hover {
    text-decoration: none;
  }
  .setup-step:hover .setup-title {
    color: var(--accent);
  }
  .setup-step.done .setup-title {
    color: var(--muted);
    text-decoration: line-through;
  }
  .setup-check {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: 1px solid var(--border2);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .setup-check.done {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-fg);
  }
  .setup-body {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }
  .setup-title {
    font-size: 14px;
    font-weight: 500;
    color: var(--fg-bright);
  }
  .setup-desc {
    font-size: 12.5px;
    color: var(--muted);
    line-height: 1.4;
  }
  .setup-step :global(.setup-chev) {
    color: var(--muted);
    flex-shrink: 0;
  }
  .list-head {
    display: flex;
    align-items: center;
    min-height: 26px;
    margin-bottom: 8px;
  }
  .archived-row {
    gap: 10px;
  }
  .archived-link {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
    min-width: 0;
    color: inherit;
    text-decoration: none;
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
