<script lang="ts">
  // Unified left rail (Cowork-style): one column holds the primary action, the
  // app nav, and the live conversation list (Recents) — the chat list no longer
  // lives inside the Chat page. Recents is scoped to the current project via the
  // shared workspace store.
  import { router, href, link, navigate } from '../lib/router.svelte'
  import { workspace, loadChats, loadProjects, projectName } from '../lib/workspace.svelte'
  import { relativeTime } from '../lib/time'
  import { api } from '../lib/api'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { toast } from '../lib/toast.svelte'
  import { errorText } from '../lib/errors'
  import { requestPalette } from '../lib/palette.svelte'
  import OnboardingChecklist from './OnboardingChecklist.svelte'

  let {
    counts,
    version,
    open = false,
    collapsed = false,
    oncollapse,
  }: {
    counts: Record<string, number | null>
    version: string
    open?: boolean
    collapsed?: boolean
    oncollapse?: () => void
  } = $props()

  // Nav icons (24px viewBox, stroke outlines) — only visible in the collapsed
  // icon rail; the expanded sidebar stays text-only. Keyed by nav item key.
  const ICONS: Record<string, string> = {
    today:
      '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/><path d="M12 15h.01"/>',
    projects:
      '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    runners:
      '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/>',
    outputs:
      '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    runs: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    agents: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    settings: '<path d="M20 7h-9M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
  }

  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

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
    navigate(
      workspace.scopeProjectId ? '/chat?project=' + encodeURIComponent(workspace.scopeProjectId) : '/chat',
    )
  }
  async function deleteChat(id: string, e: Event) {
    e.preventDefault()
    e.stopPropagation()
    if (!(await confirmDialog('Delete this chat? The SDK transcript stays on disk.'))) return
    try {
      await api.deleteChat(id)
    } catch (err) {
      toast(errorText(err), 'err')
      return
    }
    await loadChats()
    if (activeChatId === id) newChat()
  }

  // Onboarding signals (derived from real state).
  const chatDone = $derived(
    workspace.chats.length > 0 ||
      (typeof localStorage !== 'undefined' && localStorage.getItem('bran.didChat') === '1'),
  )
</script>

<aside class="sidebar shrink-0 flex flex-col" class:open class:collapsed>
  <div class="side-head px-5 py-5">
    <a
      href={href('/')}
      use:link
      class="wordmark with-crow"
      style="text-decoration: none;"
      title={collapsed ? 'bran — Today' : undefined}
    >
      <svg class="wordmark-crow" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path
          d="m10 18l-1.65 4l-1.85-.75l1.45-3.525q-2.65-.7-4.3-2.85T2 10V6q0-1.65 1.175-2.825T6 2q.55 0 1.05.187t1 .388L14 5l-4 1.5V8l7.85 5H10q-.825 0-1.412-.587T8 11H6q0 1.65 1.175 2.825T10 15h11l1 5h-2l-1-2h-5v4h-2v-4zM5.288 5.288Q5 5.575 5 6t.288.713T6 7t.713-.288T7 6t-.288-.712T6 5t-.712.288"
        />
      </svg>
      <span class="wordmark-text">bran</span>
    </a>
    <span class="ml-auto label-cap side-version">{version}</span>
    {#if oncollapse}
      <button
        class="side-collapse"
        title="{collapsed ? 'Expand' : 'Hide'} sidebar ({isMac ? '⌘' : 'Ctrl+'}B)"
        aria-label={collapsed ? 'expand sidebar' : 'hide sidebar'}
        aria-expanded={!collapsed}
        onclick={oncollapse}
      >
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" />
        </svg>
      </button>
    {/if}
  </div>

  <div class="side-actions p-3 space-y-2">
    <button
      class="btn-primary new-chat"
      style="width: 100%;"
      onclick={newChat}
      title={collapsed ? 'new chat' : undefined}
      aria-label="new chat"
    >
      <span class="nc-full">+ new chat</span>
      <span class="nc-mini" aria-hidden="true">+</span>
    </button>
    <button
      class="palette-row"
      onclick={requestPalette}
      title={collapsed ? `search (${isMac ? '⌘K' : 'Ctrl K'})` : undefined}
      aria-label="search"
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        aria-hidden="true"
      >
        <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
      </svg>
      <span class="palette-label">search</span>
      <kbd>{isMac ? '⌘K' : 'Ctrl K'}</kbd>
    </button>
  </div>

  <nav class="p-3 text-sm">
    {#each groups as group (group.label)}
      <div class="nav-group-label label-cap">{group.label}</div>
      <div class="space-y-0.5" style="margin-bottom: 10px;">
        {#each group.items as item (item.key)}
          <a
            href={href(item.to)}
            use:link
            class="nav-item"
            class:active={active === item.key}
            aria-current={active === item.key ? 'page' : undefined}
            title={collapsed ? item.label : undefined}
          >
            <!-- eslint-disable-next-line svelte/no-at-html-tags -- ICONS is a local static const -->
            <svg
              class="nav-ico"
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true">{@html ICONS[item.key]}</svg
            >
            <span class="nav-label">{item.label}</span>
            {#if counts[item.key] != null}
              <!-- Outputs is an inbox: its count means "new since last read". -->
              <span
                class="count"
                class:fresh={item.key === 'outputs'}
                title={item.key === 'outputs' ? 'new since your last visit' : undefined}
                >{counts[item.key]}</span
              >
            {/if}
          </a>
        {/each}
      </div>
    {/each}
  </nav>

  <!-- Recents: the conversation list, scoped to the current project. -->
  <div class="recents-head px-3">
    <span class="label-cap">{scopeName}</span>
    <span class="ml-auto label-cap">{workspace.chats.length}</span>
  </div>
  <div class="recents">
    {#each workspace.chats as c (c.id)}
      <!-- Delete sits BESIDE the link, not inside it — interactive content
           inside <a> is invalid HTML and confuses keyboard/screen-reader use. -->
      <div class="recent-row" class:active={activeChatId === c.id}>
        <a
          href={chatHref(c.id)}
          use:link
          class="recent-link"
          aria-current={activeChatId === c.id ? 'page' : undefined}
        >
          <span class="recent-title">{c.title}</span>
          <span class="recent-meta">
            {c.agent} · {relativeTime(c.updated_at)}
            <!-- Global Recents shows every chat, so say where each one lives. -->
            {#if !workspace.scopeProjectId && c.project_id}· {projectName(c.project_id)}{/if}
          </span>
        </a>
        <button
          class="recent-x"
          onclick={(e) => deleteChat(c.id, e)}
          title="delete"
          aria-label="delete chat {c.title}">×</button
        >
      </div>
    {:else}
      <div class="text-muted" style="padding: 12px; font-size: 12px; font-style: italic; text-align: center;">
        No conversations yet.
      </div>
    {/each}
  </div>

  <div class="side-foot mt-auto p-3 space-y-3">
    <div class="onboard-wrap">
      <OnboardingChecklist
        projectDone={(counts.projects ?? 0) > 0}
        {chatDone}
        runnerDone={(counts.runners ?? 0) > 0}
      />
    </div>
    <div class="space-y-2">
      <a
        href={href('/agents')}
        use:link
        class="nav-item"
        class:active={active === 'agents'}
        aria-current={active === 'agents' ? 'page' : undefined}
        style="font-size: 13px;"
        title={collapsed ? 'Agent library' : undefined}
      >
        <!-- eslint-disable-next-line svelte/no-at-html-tags -- ICONS is a local static const -->
        <svg
          class="nav-ico"
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true">{@html ICONS.agents}</svg
        >
        <span class="nav-label">Agent library</span>
        {#if counts.agents != null}<span class="count">{counts.agents}</span>{/if}
      </a>
      <a
        href={href('/settings')}
        use:link
        class="nav-item"
        class:active={active === 'settings'}
        aria-current={active === 'settings' ? 'page' : undefined}
        style="font-size: 13px;"
        title={collapsed ? 'Settings' : undefined}
      >
        <!-- eslint-disable-next-line svelte/no-at-html-tags -- ICONS is a local static const -->
        <svg
          class="nav-ico"
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true">{@html ICONS.settings}</svg
        >
        <span class="nav-label">Settings</span>
      </a>
    </div>
  </div>
</aside>

<style>
  .side-head {
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid var(--border);
  }
  .side-actions {
    border-bottom: 1px solid var(--border);
  }
  .side-foot {
    border-top: 1px solid var(--border);
  }
  .recents-head {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }
  /* Nav icons exist for the collapsed rail; the expanded sidebar is text-only. */
  .nav-ico {
    display: none;
    flex-shrink: 0;
  }
  .nc-mini {
    display: none;
  }

  .nav-group-label {
    padding: 2px 12px 6px;
    color: var(--muted);
  }
  .side-collapse {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    background: transparent;
    border: 0;
    border-radius: var(--radius);
    color: var(--muted);
    cursor: pointer;
    transition:
      color var(--dur-1) var(--transition),
      background var(--dur-1) var(--transition);
  }
  .side-collapse:hover {
    color: var(--fg-bright);
    background: var(--surface2);
  }
  /* Hidden on phones — the drawer closes by tapping the overlay instead. */
  @media (max-width: 768px) {
    .side-collapse {
      display: none;
    }
  }

  .palette-row {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 6px 10px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--muted);
    font-size: 12px;
    cursor: pointer;
    transition:
      color var(--dur-1) var(--transition),
      border-color var(--dur-1) var(--transition);
  }
  .palette-row:hover {
    color: var(--fg);
    border-color: var(--border2);
  }
  .palette-row kbd {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--muted);
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
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
    align-items: stretch;
    border-radius: var(--radius);
    margin-bottom: 2px;
    transition: background 0.12s var(--transition);
  }
  .recent-row:hover {
    background: var(--surface2);
  }
  .recent-row.active {
    background: var(--accent-glow);
  }
  .recent-link {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    padding: 7px 0 7px 10px;
    text-decoration: none;
  }
  .recent-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .recent-row.active .recent-title {
    color: var(--accent-soft);
  }
  .recent-x {
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    line-height: 1;
    /* full-height column: a real hit target, not a 14px sliver */
    padding: 0 8px;
    opacity: 0;
    transition: opacity var(--dur-1) var(--transition);
  }
  .recent-x:hover {
    color: var(--red);
  }
  /* Reveal on hover AND on keyboard focus — a control you can't see when
     you Tab to it may as well not exist. */
  .recent-row:hover .recent-x,
  .recent-row:focus-within .recent-x,
  .recent-x:focus-visible {
    opacity: 1;
  }
  /* No hover on touch — the control must simply be there. */
  @media (hover: none) {
    .recent-x {
      opacity: 1;
    }
  }
  .recent-meta {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* ------------------------------------------------------------------------
     Collapsed icon rail (desktop only — below 768px the off-canvas drawer
     always shows the full sidebar, so these rules stay inert there even
     though the .collapsed class may be present). Labels, counts, Recents and
     onboarding disappear; icons with title tooltips carry the nav.
     ------------------------------------------------------------------------ */
  @media (min-width: 769px) {
    .collapsed .side-head {
      flex-direction: column;
      gap: 10px;
      padding: 14px 0 12px;
    }
    .collapsed .wordmark-text,
    .collapsed .side-version {
      display: none;
    }

    .collapsed .side-actions {
      padding: 10px 8px;
    }
    .collapsed .nc-full {
      display: none;
    }
    .collapsed .nc-mini {
      display: inline;
      font-size: 15px;
      line-height: 1;
    }
    .collapsed .new-chat {
      padding: 6px 0;
    }
    .collapsed .palette-row {
      justify-content: center;
      padding: 6px 0;
    }
    .collapsed .palette-label,
    .collapsed .palette-row kbd {
      display: none;
    }

    .collapsed nav {
      padding: 8px;
    }
    .collapsed .nav-group-label {
      display: none;
    }
    .collapsed .nav-ico {
      display: block;
    }
    .collapsed .nav-label {
      display: none;
    }
    .collapsed .nav-item {
      justify-content: center;
      padding: 8px 0;
      position: relative;
    }
    .collapsed .count {
      display: none;
    }
    /* The Outputs inbox badge survives as an accent dot on the icon. */
    .collapsed .count.fresh {
      display: block;
      position: absolute;
      top: 4px;
      right: 9px;
      width: 6px;
      height: 6px;
      margin: 0;
      padding: 0;
      border-radius: 50%;
      background: var(--accent);
      font-size: 0;
      overflow: hidden;
    }

    .collapsed .recents-head,
    .collapsed .recents,
    .collapsed .onboard-wrap {
      display: none;
    }

    .collapsed .side-foot {
      padding: 8px;
    }
  }
</style>
