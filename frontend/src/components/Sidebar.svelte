<script lang="ts">
  // Unified left rail (Cowork-style): one column holds the primary action, the
  // app nav, and the live conversation list (Recents) — the chat list no longer
  // lives inside the Chat page. Recents is scoped to the current project via the
  // shared workspace store.
  import { router, href, link, navigate } from '../lib/router.svelte'
  import { THEMES, getTheme, setTheme } from '../lib/theme'
  import { workspace, loadChats, loadProjects, projectName } from '../lib/workspace.svelte'
  import { relativeTime } from '../lib/time'
  import { api } from '../lib/api'
  import { confirmDialog } from '../lib/confirm.svelte'
  import OnboardingChecklist from './OnboardingChecklist.svelte'

  let { counts, version, open = false }: { counts: Record<string, number | null>; version: string; open?: boolean } = $props()

  let theme = $state(getTheme())
  function onTheme(e: Event) {
    theme = (e.target as HTMLSelectElement).value
    setTheme(theme)
  }

  // Load Recents + project names once; pages drive scope via setScope().
  $effect(() => {
    void loadChats()
    void loadProjects()
  })

  // Two domains, surfaced as labeled groups so the IA is self-explanatory:
  // Workspace (where you work) vs Fleet (the agents and their runs).
  const groups = $derived([
    {
      label: 'Workspace',
      items: [
        // Today is the assistant's home: deliveries, failures, what's coming.
        { key: 'today', label: 'Today', to: '/' },
        { key: 'projects', label: 'Projects', to: '/projects' },
      ],
    },
    {
      // Agents (definitions) intentionally aren't here — they're plumbing, not
      // a daily surface. The library lives next to Settings at the bottom.
      label: 'Fleet',
      items: [
        { key: 'runners', label: 'Runners', to: '/runners' },
        { key: 'outputs', label: 'Outputs', to: '/outputs' },
        { key: 'runs', label: 'Runs', to: '/runs' },
      ],
    },
  ])
  const seg0 = $derived(router.route.segments[0] ?? '')
  const active = $derived(seg0 === '' ? 'today' : seg0)

  // Active conversation = /chat/:id in the URL.
  const activeChatId = $derived(seg0 === 'chat' ? (router.route.segments[1] ?? null) : null)
  const scopeName = $derived(projectName(workspace.scopeProjectId) ?? 'Recents')

  function chatHref(id: string): string {
    const q = workspace.scopeProjectId ? '?project=' + encodeURIComponent(workspace.scopeProjectId) : ''
    return href('/chat/' + encodeURIComponent(id) + q)
  }
  function newChat() {
    navigate(workspace.scopeProjectId ? '/chat?project=' + encodeURIComponent(workspace.scopeProjectId) : '/chat')
  }
  async function deleteChat(id: string, e: Event) {
    e.preventDefault()
    e.stopPropagation()
    if (!(await confirmDialog('Delete this chat? The SDK transcript stays on disk.'))) return
    await api.deleteChat(id)
    await loadChats()
    if (activeChatId === id) newChat()
  }

  // Onboarding signals (derived from real state).
  const chatDone = $derived(
    workspace.chats.length > 0 ||
    (typeof localStorage !== 'undefined' && localStorage.getItem('bran.didChat') === '1'),
  )
</script>

<aside class="sidebar shrink-0 flex flex-col" class:open>
  <div class="px-5 py-5" style="border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px;">
    <a href={href('/')} use:link class="wordmark with-crow" style="text-decoration: none;">
      <svg class="wordmark-crow" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="m10 18l-1.65 4l-1.85-.75l1.45-3.525q-2.65-.7-4.3-2.85T2 10V6q0-1.65 1.175-2.825T6 2q.55 0 1.05.187t1 .388L14 5l-4 1.5V8l7.85 5H10q-.825 0-1.412-.587T8 11H6q0 1.65 1.175 2.825T10 15h11l1 5h-2l-1-2h-5v4h-2v-4zM5.288 5.288Q5 5.575 5 6t.288.713T6 7t.713-.288T7 6t-.288-.712T6 5t-.712.288" />
      </svg>
      <span>bran</span>
    </a>
    <span class="ml-auto label-cap">{version}</span>
  </div>

  <div class="p-3" style="border-bottom: 1px solid var(--border);">
    <button class="btn-primary" style="width: 100%;" onclick={newChat}>+ new chat</button>
  </div>

  <nav class="p-3 text-sm">
    {#each groups as group}
      <div class="nav-group-label label-cap">{group.label}</div>
      <div class="space-y-0.5" style="margin-bottom: 10px;">
        {#each group.items as item}
          <a href={href(item.to)} use:link class="nav-item" class:active={active === item.key}>
            <span>{item.label}</span>
            {#if counts[item.key] != null}
              <!-- Outputs is an inbox: its count means "new since last read". -->
              <span class="count" class:fresh={item.key === 'outputs'} title={item.key === 'outputs' ? 'new since your last visit' : undefined}>{counts[item.key]}</span>
            {/if}
          </a>
        {/each}
      </div>
    {/each}
  </nav>

  <!-- Recents: the conversation list, scoped to the current project. -->
  <div class="px-3" style="display: flex; align-items: baseline; gap: 6px;">
    <span class="label-cap">{scopeName}</span>
    <span class="ml-auto label-cap">{workspace.chats.length}</span>
  </div>
  <div class="recents">
    {#each workspace.chats as c}
      <a href={chatHref(c.id)} use:link class="recent-row" class:active={activeChatId === c.id}>
        <span class="recent-title">{c.title}</span>
        <button class="recent-x" onclick={(e) => deleteChat(c.id, e)} title="delete">×</button>
        <span class="recent-meta">{c.agent} · {relativeTime(c.updated_at)}</span>
      </a>
    {:else}
      <div class="text-muted" style="padding: 12px; font-size: 12px; font-style: italic; text-align: center;">No conversations yet.</div>
    {/each}
  </div>

  <div class="mt-auto p-3 space-y-3" style="border-top: 1px solid var(--border);">
    <OnboardingChecklist
      projectDone={(counts.projects ?? 0) > 0}
      {chatDone}
      runnerDone={(counts.runners ?? 0) > 0}
    />
    <div class="space-y-2">
      <div class="label-cap px-1">Theme</div>
      <select class="field" value={theme} onchange={onTheme} style="font-size: 12px;">
        {#each THEMES as t}
          <option value={t.name}>{t.label}</option>
        {/each}
      </select>
      <a href={href('/agents')} use:link class="nav-item" class:active={active === 'agents'} style="font-size: 13px;">
        <span>Agent library</span>
        {#if counts.agents != null}<span class="count">{counts.agents}</span>{/if}
      </a>
      <a href={href('/settings')} use:link class="nav-item" class:active={active === 'settings'} style="font-size: 13px;">
        <span>Settings</span>
      </a>
    </div>
  </div>
</aside>

<style>
  .nav-group-label {
    padding: 2px 12px 6px;
    color: var(--muted);
  }
  /* "New deliveries" badge — accent-tinted so it reads as unread, not a total. */
  .count.fresh {
    color: var(--accent-soft);
    background: var(--accent-glow);
    border-radius: 999px;
    padding: 1px 7px;
  }
  .recents {
    flex: 1;
    overflow-y: auto;
    padding: 4px 8px;
    min-height: 60px;
  }
  .recent-row {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    gap: 4px 6px;
    padding: 7px 10px;
    border-radius: var(--radius);
    margin-bottom: 2px;
    text-decoration: none;
    transition: background 0.12s var(--transition);
  }
  .recent-row:hover { background: var(--surface2); }
  .recent-row.active { background: var(--accent-glow); }
  .recent-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .recent-row.active .recent-title { color: var(--accent-soft); }
  .recent-x {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    line-height: 1;
    padding: 0 2px;
    opacity: 0;
    transition: opacity 0.12s var(--transition);
  }
  .recent-row:hover .recent-x { opacity: 1; }
  .recent-meta {
    grid-column: 1 / -1;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
