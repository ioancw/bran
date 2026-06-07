<script lang="ts">
  // Project home, modelled on Cowork: a launcher and the project's
  // conversations (as cards) in the center, the project's settings/context as a
  // single narrow rail on the right.
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { setScope } from '../lib/workspace.svelte'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { errorText } from '../lib/errors'
  import { relativeTime } from '../lib/time'
  import Page from '../components/Page.svelte'
  import ProjectRail from '../components/ProjectRail.svelte'
  import type { ProjectDetail } from '../lib/types'

  let { projectId }: { projectId: string } = $props()

  let data = $state<ProjectDetail | null>(null)
  let error = $state<string | null>(null)
  let name = $state('')
  let prompt = $state('')

  async function load() {
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

  function launch() {
    const t = prompt.trim()
    const q = t ? '&prompt=' + encodeURIComponent(t) : ''
    navigate('/chat?project=' + encodeURIComponent(projectId) + q)
  }
  function chatHref(id: string): string {
    return href('/chat/' + encodeURIComponent(id) + '?project=' + encodeURIComponent(projectId))
  }
  async function deleteProject() {
    if (!(await confirmDialog(`Delete project "${name}"? Its chats become loose; nothing is lost.`))) return
    await api.deleteProject(projectId)
    setScope(null)
    navigate('/')
  }
</script>

<Page title={name || 'Project'}>
  {#snippet subtitle()}workspace{/snippet}
  {#snippet actions()}
    <button class="btn-outline" onclick={deleteProject}>delete</button>
  {/snippet}

  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !data && !error}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {/if}
  {#if data}
    <div class="ph-grid">
      <!-- Center: launcher + conversations (takes the remaining width) -->
      <div class="space-y-6">
        <div class="composer">
          <textarea class="composer-input" bind:value={prompt} rows="3"
                    placeholder={`What would you like to work on in ${name}?`}
                    onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); launch() } }}></textarea>
          <div class="composer-footer">
            <span class="composer-hint">⏎ to start · ⇧⏎ newline</span>
            <button class="composer-send" onclick={launch} aria-label="Start a conversation">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          </div>
        </div>

        <section>
          <div class="label-cap" style="margin-bottom: 10px;">Recents ({data.chats.length})</div>
          {#if data.chats.length}
            <div class="space-y-2">
              {#each data.chats as c}
                <a href={chatHref(c.id)} use:link class="conv-card card">
                  <span class="conv-icon" aria-hidden="true">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>
                  </span>
                  <div style="flex: 1; min-width: 0;">
                    <div class="conv-title">{c.title}</div>
                    <div class="conv-meta">{c.agent}</div>
                  </div>
                  <span class="conv-time label-cap">{relativeTime(c.updated_at)}</span>
                </a>
              {/each}
            </div>
          {:else}
            <div class="text-muted" style="font-size: 13px; font-style: italic;">No conversations yet — start one above.</div>
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
    .ph-grid { grid-template-columns: 1fr; }
  }
  .conv-card {
    display: flex;
    align-items: center;
    gap: 12px;
    text-decoration: none;
    transition: border-color 0.15s var(--transition), transform 0.12s var(--transition);
  }
  .conv-card:hover {
    transform: translateY(-1px);
    border-color: var(--accent-soft);
  }
  .conv-icon {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    flex-shrink: 0;
    border-radius: 50%;
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
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--accent-soft);
    margin-top: 2px;
  }
  .conv-time {
    flex-shrink: 0;
  }
</style>
