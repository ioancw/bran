<script lang="ts">
  // Unified left rail: wordmark, primary action, search, the app nav, and the
  // live conversation list (Recents) scoped to the current project. Collapses
  // to a 56px icon rail on desktop (Ctrl/Cmd+B); becomes an off-canvas drawer
  // on phones.
  import { router, href, link, navigate } from '../lib/router.svelte'
  import { workspace, loadChats, loadProjects, projectName } from '../lib/workspace.svelte'
  import { relativeTime } from '../lib/time'
  import { api } from '../lib/api'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { toast } from '../lib/toast.svelte'
  import { errorText } from '../lib/errors'
  import { requestPalette } from '../lib/palette.svelte'
  import Icon from './Icon.svelte'
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

  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

  // Load Recents + project names once; pages drive scope via setScope().
  $effect(() => {
    void loadChats()
    void loadProjects()
  })

  // Two domains, surfaced as labeled groups so the IA is self-explanatory:
  // Workspace (where you work) vs Fleet (the agents and their runs).
  const groups = [
    {
      label: 'Workspace',
      items: [
        { key: 'today', label: 'Today', to: '/', icon: 'calendar' },
        { key: 'projects', label: 'Projects', to: '/projects', icon: 'folder' },
      ],
    },
    {
      label: 'Fleet',
      items: [
        { key: 'runners', label: 'Runners', to: '/runners', icon: 'bot' },
        { key: 'outputs', label: 'Outputs', to: '/outputs', icon: 'inbox' },
        { key: 'runs', label: 'Runs', to: '/runs', icon: 'history' },
      ],
    },
  ]
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
  async function deleteChat(id: string, title: string, e: Event) {
    e.preventDefault()
    e.stopPropagation()
    if (
      !(await confirmDialog(
        `Delete "${title}"? It disappears from Recents. The transcript file stays on disk.`,
        'Delete chat',
      ))
    )
      return
    try {
      await api.deleteChat(id)
    } catch (err) {
      toast(errorText(err), 'err')
      return
    }
    await loadChats()
    if (activeChatId === id) newChat()
  }

  // --- Archive: out of Recents, kept whole, one click back -----------------
  let archivedOpen = $state(false)
  async function setArchived(id: string, archived: boolean) {
    closeMenu()
    try {
      await api.archiveChat(id, archived)
    } catch (err) {
      toast(errorText(err), 'err')
      return
    }
    toast(archived ? 'Archived' : 'Restored to Recents', 'ok')
    await loadChats()
    if (archived && activeChatId === id) newChat()
  }

  // --- Per-chat "…" menu: rename / move to project / archive / delete -------
  let menuFor = $state<string | null>(null) // chat id whose menu is open
  let moveOpen = $state(false) // the "Move to project" list is expanded
  let renamingId = $state<string | null>(null)
  let renameText = $state('')
  let renameInput = $state<HTMLInputElement | null>(null)
  let renameSaving = false

  function openMenu(id: string, e: Event) {
    e.preventDefault()
    e.stopPropagation()
    moveOpen = false
    menuFor = menuFor === id ? null : id
  }
  function closeMenu() {
    menuFor = null
    moveOpen = false
  }
  function onWindowClick(e: MouseEvent) {
    if (menuFor === null) return
    const t = e.target as HTMLElement | null
    if (t?.closest('.recent-menu, .recent-more')) return
    closeMenu()
  }
  function onWindowKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && menuFor !== null) {
      e.preventDefault()
      closeMenu()
    }
  }

  function startRename(c: { id: string; title: string }) {
    closeMenu()
    renamingId = c.id
    renameText = c.title
    requestAnimationFrame(() => {
      renameInput?.focus()
      renameInput?.select()
    })
  }
  async function commitRename(c: { id: string; title: string }) {
    if (renamingId !== c.id || renameSaving) return
    const title = renameText.trim()
    renamingId = null
    if (!title || title === c.title) return
    renameSaving = true
    try {
      await api.renameChat(c.id, title)
      await loadChats()
    } catch (err) {
      toast(errorText(err), 'err')
    } finally {
      renameSaving = false
    }
  }
  function cancelRename() {
    renamingId = null
  }
  function onRenameKeydown(e: KeyboardEvent, c: { id: string; title: string }) {
    if (e.key === 'Enter') {
      e.preventDefault()
      void commitRename(c)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancelRename()
    }
  }

  async function moveChat(id: string, projectId: string | null) {
    closeMenu()
    try {
      await api.moveChat(id, projectId)
      toast(projectId ? `Moved to ${projectName(projectId)}` : 'Moved out of project', 'ok')
    } catch (err) {
      toast(errorText(err), 'err')
      return
    }
    await Promise.all([loadChats(), loadProjects()])
  }

  // Onboarding signals (derived from real state).
  const chatDone = $derived(
    workspace.chats.length > 0 ||
      (typeof localStorage !== 'undefined' && localStorage.getItem('bran.didChat') === '1'),
  )
</script>

<svelte:window onclick={onWindowClick} onkeydown={onWindowKeydown} />

<aside class="sidebar shrink-0 flex flex-col" class:open class:collapsed>
  <div class="side-head">
    <a href={href('/')} use:link class="wordmark" title="bran {version}">
      <svg class="wordmark-crow" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path
          d="m10 18l-1.65 4l-1.85-.75l1.45-3.525q-2.65-.7-4.3-2.85T2 10V6q0-1.65 1.175-2.825T6 2q.55 0 1.05.187t1 .388L14 5l-4 1.5V8l7.85 5H10q-.825 0-1.412-.587T8 11H6q0 1.65 1.175 2.825T10 15h11l1 5h-2l-1-2h-5v4h-2v-4zM5.288 5.288Q5 5.575 5 6t.288.713T6 7t.713-.288T7 6t-.288-.712T6 5t-.712.288"
        />
      </svg>
      <span class="wordmark-text">bran</span>
    </a>
    {#if oncollapse}
      <button
        class="btn-icon side-collapse"
        title="{collapsed ? 'Expand' : 'Hide'} sidebar ({isMac ? '⌘' : 'Ctrl+'}B)"
        aria-label={collapsed ? 'Expand sidebar' : 'Hide sidebar'}
        aria-expanded={!collapsed}
        onclick={oncollapse}
      >
        <Icon name="panel-left" size={16} />
      </button>
    {/if}
  </div>

  <div class="side-actions">
    <button
      class="btn-primary new-chat"
      onclick={newChat}
      title={collapsed ? 'New chat' : undefined}
      aria-label="New chat"
    >
      <Icon name="plus" size={15} stroke={2.2} />
      <span class="nav-label">New chat</span>
    </button>
    <button
      class="palette-row"
      onclick={requestPalette}
      title={collapsed ? `Search (${isMac ? '⌘K' : 'Ctrl K'})` : undefined}
      aria-label="Search"
    >
      <Icon name="search" size={15} />
      <span class="nav-label palette-label">Search</span>
      <kbd class="nav-label">{isMac ? '⌘K' : 'Ctrl K'}</kbd>
    </button>
  </div>

  <nav class="side-nav">
    {#each groups as group (group.label)}
      <div class="nav-group-label label-cap">{group.label}</div>
      <div class="nav-group">
        {#each group.items as item (item.key)}
          <a
            href={href(item.to)}
            use:link
            class="nav-item"
            class:active={active === item.key}
            aria-current={active === item.key ? 'page' : undefined}
            title={collapsed ? item.label : undefined}
          >
            <Icon name={item.icon} size={16} class="nav-ico" />
            <span class="nav-label">{item.label}</span>
            {#if counts[item.key] != null}
              <!-- Outputs is an inbox: its count means "new since last read". -->
              <span
                class="count"
                class:fresh={item.key === 'outputs'}
                title={item.key === 'outputs' ? 'New since your last visit' : undefined}
                >{counts[item.key]}</span
              >
            {/if}
          </a>
        {/each}
      </div>
    {/each}
  </nav>

  <!-- Recents: the conversation list, scoped to the current project. -->
  <div class="recents-head">
    <span class="label-cap recents-title">{scopeName}</span>
    <span class="recents-count">{workspace.chats.length}</span>
  </div>
  <div class="recents">
    {#each workspace.chats as c (c.id)}
      <!-- Delete sits BESIDE the link, not inside it — interactive content
           inside <a> is invalid HTML and confuses keyboard/screen-reader use. -->
      <div class="recent-row" class:active={activeChatId === c.id} class:menu-open={menuFor === c.id}>
        {#if renamingId === c.id}
          <div class="recent-rename">
            <input
              class="field"
              bind:this={renameInput}
              bind:value={renameText}
              aria-label="Rename chat"
              onkeydown={(e) => onRenameKeydown(e, c)}
              onblur={() => commitRename(c)}
            />
          </div>
        {:else}
          <a
            href={chatHref(c.id)}
            use:link
            class="recent-link"
            aria-current={activeChatId === c.id ? 'page' : undefined}
          >
            <span class="recent-title">{c.title}</span>
            <span class="recent-meta">
              <span>{relativeTime(c.updated_at)}</span>
              <!-- Global Recents shows every chat, so say where each one lives. -->
              {#if !workspace.scopeProjectId && c.project_id}<span class="recent-proj"
                  >{projectName(c.project_id)}</span
                >{/if}
            </span>
          </a>
          <button
            class="btn-icon recent-more"
            onclick={(e) => openMenu(c.id, e)}
            title="Chat options"
            aria-label="Options for chat {c.title}"
            aria-haspopup="menu"
            aria-expanded={menuFor === c.id}
          >
            <Icon name="more" size={14} />
          </button>
          {#if menuFor === c.id}
            <div class="recent-menu card elev-md" role="menu" aria-label="Chat options">
              <button class="menu-item" role="menuitem" onclick={() => startRename(c)}>
                <Icon name="pencil" size={13} />Rename
              </button>
              <button
                class="menu-item"
                role="menuitem"
                aria-expanded={moveOpen}
                onclick={() => (moveOpen = !moveOpen)}
              >
                <Icon name="folder" size={13} />Move to project
                <Icon name={moveOpen ? 'chevron-up' : 'chevron-down'} size={12} class="menu-chev" />
              </button>
              {#if moveOpen}
                <div class="menu-sub" role="group" aria-label="Projects">
                  {#each workspace.projects as p (p.id)}
                    <button
                      class="menu-item sub"
                      role="menuitemradio"
                      aria-checked={c.project_id === p.id}
                      onclick={() => moveChat(c.id, p.id)}
                    >
                      <span class="menu-check"
                        >{#if c.project_id === p.id}<Icon name="check" size={12} />{/if}</span
                      >{p.name}
                    </button>
                  {/each}
                  <button
                    class="menu-item sub"
                    role="menuitemradio"
                    aria-checked={!c.project_id}
                    onclick={() => moveChat(c.id, null)}
                  >
                    <span class="menu-check"
                      >{#if !c.project_id}<Icon name="check" size={12} />{/if}</span
                    >No project
                  </button>
                </div>
              {/if}
              <button class="menu-item" role="menuitem" onclick={() => setArchived(c.id, true)}>
                <Icon name="archive" size={13} />Archive
              </button>
              <button
                class="menu-item danger"
                role="menuitem"
                onclick={(e) => {
                  closeMenu()
                  void deleteChat(c.id, c.title, e)
                }}
              >
                <Icon name="trash" size={13} />Delete
              </button>
            </div>
          {/if}
        {/if}
      </div>
    {:else}
      <div class="recents-empty">No conversations yet.</div>
    {/each}

    {#if workspace.archived.length}
      <!-- Archived chats stay out of the way until asked for. -->
      <button
        class="archived-toggle"
        onclick={() => (archivedOpen = !archivedOpen)}
        aria-expanded={archivedOpen}
      >
        <Icon name={archivedOpen ? 'chevron-down' : 'chevron-right'} size={12} />
        <span>Archived</span>
        <span class="recents-count">{workspace.archived.length}</span>
      </button>
      {#if archivedOpen}
        {#each workspace.archived as c (c.id)}
          <div class="recent-row archived" class:active={activeChatId === c.id}>
            <a
              href={chatHref(c.id)}
              use:link
              class="recent-link"
              aria-current={activeChatId === c.id ? 'page' : undefined}
            >
              <span class="recent-title">{c.title}</span>
              <span class="recent-meta"><span>{relativeTime(c.archived_at ?? c.updated_at)}</span></span>
            </a>
            <button
              class="btn-icon recent-more"
              onclick={() => setArchived(c.id, false)}
              title="Restore to Recents"
              aria-label="Restore chat {c.title}"
            >
              <Icon name="archive-restore" size={14} />
            </button>
          </div>
        {/each}
      {/if}
    {/if}
  </div>

  <div class="side-foot">
    <div class="onboard-wrap">
      <OnboardingChecklist
        projectDone={(counts.projects ?? 0) > 0}
        {chatDone}
        runnerDone={(counts.runners ?? 0) > 0}
      />
    </div>
    <div class="nav-group">
      <a
        href={href('/agents')}
        use:link
        class="nav-item"
        class:active={active === 'agents'}
        aria-current={active === 'agents' ? 'page' : undefined}
        title={collapsed ? 'Agent library' : undefined}
      >
        <Icon name="book" size={16} class="nav-ico" />
        <span class="nav-label">Agent library</span>
        {#if counts.agents != null}<span class="count">{counts.agents}</span>{/if}
      </a>
      <a
        href={href('/settings')}
        use:link
        class="nav-item"
        class:active={active === 'settings'}
        aria-current={active === 'settings' ? 'page' : undefined}
        title={collapsed ? 'Settings' : undefined}
      >
        <Icon name="sliders" size={16} class="nav-ico" />
        <span class="nav-label">Settings</span>
      </a>
    </div>
  </div>
</aside>

<style>
  .side-head {
    display: flex;
    align-items: center;
    gap: 8px;
    height: var(--header-h);
    padding: 0 12px 0 16px;
    flex-shrink: 0;
  }
  .side-head .wordmark:hover {
    text-decoration: none;
  }
  .side-collapse {
    margin-left: auto;
  }
  /* Hidden on phones — the drawer closes by tapping the overlay instead. */
  @media (max-width: 768px) {
    .side-collapse {
      display: none;
    }
  }

  .side-actions {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 4px 12px 12px;
  }
  .new-chat {
    width: 100%;
    justify-content: center;
  }
  .palette-row {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    height: 32px;
    padding: 0 10px;
    background: var(--surface2);
    border: 1px solid transparent;
    border-radius: var(--radius);
    color: var(--muted);
    font-size: 13px;
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
    background: var(--surface);
  }

  .side-nav {
    padding: 0 12px;
  }
  .nav-group {
    display: flex;
    flex-direction: column;
    gap: 1px;
    margin-bottom: 12px;
  }
  .nav-group-label {
    display: block;
    padding: 4px 10px 4px;
    font-size: 11px;
  }
  /* "New deliveries" badge — accent-tinted so it reads as unread, not a total. */
  .count.fresh {
    color: var(--accent-fg);
    background: var(--accent);
    border-radius: 999px;
    padding: 0 6px;
    font-size: 11px;
    font-weight: 600;
    line-height: 18px;
  }
  :global(.sidebar .nav-item.active .count.fresh) {
    color: var(--accent-fg);
  }

  .recents-head {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 22px 4px;
  }
  .recents-count {
    margin-left: auto;
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .recents {
    flex: 1;
    overflow-y: auto;
    padding: 0 12px 8px;
    min-height: 60px;
  }
  .archived-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
    margin-top: 8px;
    padding: 6px 10px;
    background: transparent;
    border: 0;
    border-top: 1px solid var(--border);
    border-radius: 0;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
    cursor: pointer;
    text-align: left;
  }
  .archived-toggle:hover {
    color: var(--fg-bright);
  }
  .recent-row.archived .recent-title {
    color: var(--fg-dim);
  }
  .recents-empty {
    padding: 12px 10px;
    font-size: 12px;
    color: var(--muted);
  }
  .recent-row {
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: center;
    border-radius: var(--radius);
    margin-bottom: 1px;
    padding-right: 2px;
    transition: background var(--dur-1) var(--transition);
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
    gap: 2px;
    min-width: 0;
    padding: 6px 4px 6px 10px;
    text-decoration: none;
  }
  .recent-link:hover {
    text-decoration: none;
  }
  .recent-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .recent-row.active .recent-title {
    color: var(--accent-soft);
  }
  .recent-meta {
    display: flex;
    gap: 6px;
    min-width: 0;
    font-size: 11px;
    color: var(--muted);
  }
  .recent-proj {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .recent-proj::before {
    content: '·';
    margin-right: 6px;
  }
  .recent-more {
    opacity: 0;
    transition: opacity var(--dur-1) var(--transition);
  }
  /* Reveal on hover AND on keyboard focus — a control you can't see when
     you Tab to it may as well not exist. Stays visible while its menu is open. */
  .recent-row:hover .recent-more,
  .recent-row:focus-within .recent-more,
  .recent-row.menu-open .recent-more,
  .recent-more:focus-visible {
    opacity: 1;
  }
  /* No hover on touch — the control must simply be there. */
  @media (hover: none) {
    .recent-more {
      opacity: 1;
    }
  }
  .recent-row {
    position: relative;
  }
  .recent-rename {
    grid-column: 1 / -1;
    padding: 4px 4px 4px 6px;
  }
  .recent-rename .field {
    padding: 4px 8px;
    font-size: 13px;
  }
  .recent-menu {
    position: absolute;
    top: calc(100% - 2px);
    right: 4px;
    z-index: 80;
    min-width: 190px;
    padding: 4px;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .menu-item {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    padding: 6px 8px;
    background: transparent;
    border: 0;
    border-radius: var(--radius);
    color: var(--fg);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  .menu-item:hover,
  .menu-item:focus-visible {
    background: var(--surface2);
  }
  .menu-item.danger:hover,
  .menu-item.danger:focus-visible {
    color: var(--red);
    background: var(--red-soft);
  }
  .menu-item :global(.menu-chev) {
    margin-left: auto;
    color: var(--muted);
  }
  .menu-sub {
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 2px 0 2px 6px;
    max-height: 220px;
    overflow-y: auto;
  }
  .menu-item.sub {
    font-size: 12.5px;
    padding: 5px 8px;
  }
  .menu-check {
    display: inline-flex;
    width: 14px;
    justify-content: center;
    color: var(--accent-soft);
    flex-shrink: 0;
  }

  .side-foot {
    margin-top: auto;
    padding: 8px 12px 10px;
    padding-bottom: calc(10px + var(--safe-bottom));
    border-top: 1px solid var(--border);
  }
  .side-foot .nav-group {
    margin-bottom: 0;
  }
  .onboard-wrap:not(:empty) {
    margin-bottom: 8px;
  }

  /* ------------------------------------------------------------------------
     Collapsed icon rail (desktop only — below 768px the off-canvas drawer
     always shows the full sidebar). Labels, counts, Recents and onboarding
     disappear; icons with title tooltips carry the nav.
     ------------------------------------------------------------------------ */
  @media (min-width: 769px) {
    .collapsed .side-head {
      flex-direction: column;
      justify-content: center;
      gap: 6px;
      height: auto;
      padding: 10px 0 8px;
    }
    .collapsed .wordmark-text {
      display: none;
    }
    .collapsed .side-collapse {
      margin-left: 0;
    }
    .collapsed .side-actions {
      padding: 4px 10px 10px;
    }
    .collapsed .nav-label {
      display: none;
    }
    .collapsed .new-chat {
      padding: 0;
    }
    .collapsed .palette-row {
      justify-content: center;
      padding: 0;
    }
    .collapsed .side-nav {
      padding: 0 10px;
    }
    .collapsed .nav-group-label {
      display: none;
    }
    .collapsed .nav-item {
      justify-content: center;
      padding: 0;
      position: relative;
    }
    .collapsed .count {
      display: none;
    }
    /* The Outputs inbox badge survives as an accent dot on the icon. */
    .collapsed .count.fresh {
      display: block;
      position: absolute;
      top: 5px;
      right: 8px;
      width: 7px;
      height: 7px;
      margin: 0;
      padding: 0;
      border-radius: 50%;
      background: var(--accent);
      font-size: 0;
      line-height: 0;
      overflow: hidden;
    }
    .collapsed .recents-head,
    .collapsed .recents,
    .collapsed .onboard-wrap {
      display: none;
    }
    .collapsed .side-foot {
      padding: 8px 10px 10px;
    }
  }
</style>
