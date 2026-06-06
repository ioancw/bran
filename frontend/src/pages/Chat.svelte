<script lang="ts">
  import { api, streamChat } from '../lib/api'
  import { router, navigate, href, link } from '../lib/router.svelte'
  import { workspace, setScope, loadChats, loadProjects, projectName, bumpActivity, setLive } from '../lib/workspace.svelte'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import ProjectRail from '../components/ProjectRail.svelte'
  import ChatLog from '../chat/ChatLog.svelte'
  import { applyEvent, freshState, type ChatItem, type ReducerState } from '../chat/events'
  import type { Catalog, ChatSummary } from '../lib/types'

  let { sessionId }: { sessionId: string | null } = $props()

  let catalog = $state<Catalog>({ agents: [], commands: [] })
  let activeChat = $state<ChatSummary | null>(null)
  let items = $state<ChatItem[]>([])
  let streaming = $state(false)
  let streamingIndex = $state(-1)
  let pendingAgent = $state<string | null>(null)
  let input = $state('')

  let rstate: ReducerState = freshState()
  let loadedId: string | null = null
  let autoSent = false
  let messagesEl: HTMLDivElement | undefined = $state()

  // Scope = the project whose conversations the rail + sidebar show (null =
  // loose). It lives in the shared workspace store now; a chat is viewed inside
  // its project (the active chat's project is authoritative), else the ?project=
  // query. Initialise synchronously so there's no loose-then-rescope flash.
  setScope(router.route.query.get('project'))
  const scopeId = $derived(workspace.scopeProjectId)
  const scopeName = $derived(projectName(scopeId) ?? 'Recents')

  const currentAgent = $derived(activeChat?.agent ?? pendingAgent ?? 'orchestrator')

  // Breadcrumb scope switcher.
  let scopeMenuOpen = $state(false)
  function switchScope(id: string | null) {
    scopeMenuOpen = false
    navigate(id ? '/chat?project=' + encodeURIComponent(id) : '/chat')
  }

  function scrollSoon() {
    requestAnimationFrame(() => messagesEl?.scrollTo(0, messagesEl.scrollHeight))
  }

  $effect(() => {
    void api.catalog().then((c) => (catalog = c)).catch(() => {})
    void loadProjects()
  })

  // React to the active session id (route change). Skip our own URL pivot
  // (loadedId already set) and never reload mid-stream.
  $effect(() => {
    const sid = sessionId
    if (sid === loadedId || streaming) return
    loadedId = sid
    items = []
    rstate = freshState()
    pendingAgent = null
    if (sid) {
      void (async () => {
        try {
          const c = await api.chat(sid)
          activeChat = c
          setScope(c.project_id) // a chat is scoped to its project (null = loose)
        } catch {
          activeChat = null
        }
        await loadHistory(sid)
      })()
    } else {
      activeChat = null
      setScope(router.route.query.get('project'))
    }
  })

  // The breadcrumb title comes from `activeChat`, but a freshly-created chat
  // starts as "(new)" until the backend generates a title. Once Recents
  // refreshes with the real title, fold it back into the breadcrumb.
  $effect(() => {
    if (!activeChat) return
    const fresh = workspace.chats.find((c) => c.id === activeChat!.id)
    if (fresh && fresh.title !== activeChat.title) {
      activeChat = { ...activeChat, title: fresh.title }
    }
  })

  // Project launcher: arriving with ?prompt=… auto-sends the first message.
  $effect(() => {
    const p = router.route.query.get('prompt')
    if (p && !sessionId && !autoSent && !streaming) {
      autoSent = true
      input = p
      void send()
    }
  })

  async function loadHistory(sid: string) {
    try {
      const { events } = await api.history(sid)
      const next: ChatItem[] = []
      const st = freshState()
      for (const ev of events) applyEvent(next, st, ev)
      items = next
      scrollSoon()
    } catch {
      /* ignore */
    }
  }

  async function send() {
    const text = input.trim()
    if (!text || streaming) return
    input = ''
    items.push({ kind: 'user', text })
    streaming = true
    setLive(true, 'thinking…')
    scrollSoon()

    const fields: Record<string, string> = { prompt: text }
    if (activeChat) {
      fields.session_id = activeChat.id
      fields.chat_agent = activeChat.agent
      if (activeChat.project_id) fields.project_id = activeChat.project_id
    } else {
      if (pendingAgent) fields.chat_agent = pendingAgent
      const proj = router.route.query.get('project')
      if (proj) fields.project_id = proj // else loose
    }

    try {
      for await (const ev of streamChat(fields)) {
        if (ev.type === 'done') break
        // Reflect what the agent is doing into the rail's live Progress row.
        if (ev.type === 'tool_use') setLive(true, `using ${ev.name}`)
        else if (ev.type === 'thinking') setLive(true, 'thinking…')
        else if (ev.type === 'text') setLive(true, 'responding…')
        const r = applyEvent(items, rstate, ev)
        if (r.session && !activeChat) {
          const id = r.session
          const proj = router.route.query.get('project')
          activeChat = {
            id, title: '(new)', agent: pendingAgent ?? 'orchestrator',
            project_id: proj, updated_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
          }
          setScope(proj)
          try { localStorage.setItem('bran.didChat', '1') } catch { /* ignore */ }
          loadedId = id // pre-set so the route effect won't reload + wipe
          navigate('/chat/' + id)
        }
        streamingIndex = rstate.openAssistant
        scrollSoon()
      }
    } catch (e) {
      items.push({ kind: 'error', message: errorText(e) })
    } finally {
      streaming = false
      streamingIndex = -1
      setLive(false)
      void loadChats() // refresh the sidebar Recents
      bumpActivity() // refresh the rail's Progress (new run recorded)
    }
  }

  // --- Autocomplete (/ commands, @ agents) ---
  interface AcItem { trigger: string; name: string; description: string; token: string }
  let acOpen = $state(false)
  let acItems = $state<AcItem[]>([])
  let acIndex = $state(0)

  function refreshAc() {
    const m = input.match(/^([/@])(\S*)$/)
    if (!m) {
      acOpen = false
      return
    }
    const [, trigger, q] = m
    const query = q.toLowerCase()
    if (trigger === '/') {
      acItems = catalog.commands
        .filter((c) => c.name.toLowerCase().startsWith(query))
        .map((c) => ({ trigger: '/', name: c.name, description: c.description, token: `/${c.name} ` }))
    } else {
      acItems = catalog.agents
        .filter((a) => a.name.toLowerCase().includes(query))
        .map((a) => ({ trigger: '@', name: a.name, description: a.description, token: `@${a.name} ` }))
    }
    acIndex = 0
    acOpen = acItems.length > 0
  }
  function pickAc(i: number) {
    const it = acItems[i]
    if (!it) return
    input = it.token
    acOpen = false
  }
  function onKeydown(e: KeyboardEvent) {
    if (acOpen) {
      if (e.key === 'Escape') { e.preventDefault(); acOpen = false; return }
      if (e.key === 'ArrowDown') { e.preventDefault(); acIndex = Math.min(acItems.length - 1, acIndex + 1); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); acIndex = Math.max(0, acIndex - 1); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickAc(acIndex); return }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send()
    }
  }
</script>

<Page fill={true}>
  {#snippet head()}
    <!-- Cowork-style breadcrumb: <scope ▾> / conversation -->
    <div class="bc">
      <span class="crumb-wrap">
        <button class="crumb crumb-scope" onclick={() => (scopeMenuOpen = !scopeMenuOpen)}>
          {scopeName}
          <svg width="11" height="11" viewBox="0 0 10 10" fill="none" aria-hidden="true" style="margin-left: 4px;">
            <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        {#if scopeMenuOpen}
          <div class="crumb-menu card">
            <button class="crumb-opt" class:on={!scopeId} onclick={() => switchScope(null)}>Recents (loose chats)</button>
            {#each workspace.projects as p}
              <button class="crumb-opt" class:on={scopeId === p.id} onclick={() => switchScope(p.id)}>{p.name}</button>
            {/each}
          </div>
        {/if}
      </span>
      <span class="crumb-sep">/</span>
      <span class="crumb-title">{activeChat?.title ?? 'New chat'}</span>
      {#if activeChat}<span class="crumb-agent mono">{activeChat.agent}</span>{/if}
    </div>
    <span class="ml-auto thinking">{streaming ? 'thinking…' : ''}</span>
  {/snippet}

  <div style="display: flex; gap: 16px; flex: 1; min-height: 0;">
    <!-- Center: conversation + composer -->
    <div style="flex: 1; min-width: 0; display: flex; flex-direction: column;">
      <div bind:this={messagesEl} class="space-y-3" style="flex: 1; overflow-y: auto; padding-right: 6px;">
        {#if !items.length}
          <div class="empty-state" style="padding: 80px 24px;">
            <div class="brackets">[       ]</div>
            <h3>{currentAgent !== 'orchestrator' ? `chat with ${currentAgent}` : 'start a conversation'}</h3>
            <p class="cta">type a message below — try <code>/digest</code> or <code>@research</code></p>
            {#if !activeChat}
              <div style="margin-top: 16px; display: inline-flex; align-items: center; gap: 8px;">
                <span class="label-cap">agent</span>
                <select class="field" bind:value={pendingAgent} style="font-size: 12px; width: auto;">
                  <option value={null}>orchestrator</option>
                  {#each catalog.agents as a}<option value={a.name}>{a.name}</option>{/each}
                </select>
              </div>
            {/if}
          </div>
        {:else}
          <ChatLog {items} {streamingIndex} />
        {/if}
      </div>

      <!-- Composer -->
      <div style="margin-top: 14px; position: relative;">
        {#if acOpen}
          <div class="card" style="position: absolute; bottom: 100%; left: 0; right: 0; margin-bottom: 6px; padding: 4px; max-height: 280px; overflow-y: auto; z-index: 50;">
            {#each acItems as it, i}
              <button type="button" onmousedown={(e) => { e.preventDefault(); pickAc(i) }}
                      style="display: flex; gap: 10px; width: 100%; text-align: left; padding: 8px 10px; border: 0; border-radius: var(--radius); cursor: pointer; background: {i === acIndex ? 'var(--accent-glow)' : 'transparent'};">
                <code class="font-mono" style="font-size: 13px; min-width: 110px; color: {i === acIndex ? 'var(--accent-soft)' : 'var(--fg-bright)'};">{it.trigger}{it.name}</code>
                <span class="text-dim" style="font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{it.description}</span>
              </button>
            {/each}
          </div>
        {/if}
        <div class="card-quiet" style="padding: 10px 12px;">
          <div style="display: flex; gap: 10px; align-items: flex-end;">
            <textarea bind:value={input} oninput={refreshAc} onkeydown={onKeydown}
                      rows="2" placeholder="Send a message… (try / or @)" class="field"
                      style="resize: none; font-family: var(--font-prose); font-size: 15px;"></textarea>
            <button class="btn-primary" disabled={streaming} onclick={send} style="white-space: nowrap;">send ↵</button>
          </div>
          <div class="text-muted" style="font-size: 10px; font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.12em; margin-top: 6px;">
            ⏎ send · ⇧⏎ newline · / commands · @ agents · {currentAgent}
          </div>
        </div>
      </div>
    </div>

    <!-- Right rail: project context (Cowork-style), only when in a project. -->
    {#if scopeId}
      <aside class="chat-rail">
        <div style="margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px;">
          <a href={href('/projects/' + encodeURIComponent(scopeId))} use:link class="text-bright" style="font-weight: 600; text-decoration: none;">{scopeName}</a>
          <span class="label-cap">project</span>
        </div>
        <ProjectRail projectId={scopeId} />
      </aside>
    {/if}
  </div>
</Page>

<style>
  /* Breadcrumb bar — prose, prominent, like Cowork's center-top crumb. */
  .bc {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-family: var(--font-prose);
    font-size: 20px;
    letter-spacing: -0.01em;
    min-width: 0;
  }
  .crumb-wrap { position: relative; display: inline-block; }
  .crumb {
    background: transparent;
    border: 0;
    cursor: pointer;
    font: inherit;
    display: inline-flex;
    align-items: center;
    padding: 0;
  }
  .crumb-scope { color: var(--muted); }
  .crumb-scope:hover { color: var(--accent-soft); }
  .crumb-sep { color: var(--border2); }
  .crumb-title {
    color: var(--fg-bright);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .crumb-agent {
    font-size: 11px;
    color: var(--accent-soft);
    align-self: center;
  }
  .thinking {
    font-size: 11px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted);
  }
  .crumb-menu {
    position: absolute;
    top: 100%;
    left: 0;
    margin-top: 6px;
    padding: 4px;
    min-width: 200px;
    z-index: 60;
    display: flex;
    flex-direction: column;
  }
  .crumb-opt {
    text-align: left;
    background: transparent;
    border: 0;
    border-radius: var(--radius);
    padding: 7px 10px;
    cursor: pointer;
    color: var(--fg);
    font-size: 13px;
    text-transform: none;
    letter-spacing: 0;
    font-family: var(--font-ui);
  }
  .crumb-opt:hover { background: var(--surface2); }
  .crumb-opt.on { color: var(--accent-soft); }
  .chat-rail {
    width: 320px;
    flex-shrink: 0;
    overflow-y: auto;
    padding-left: 4px;
  }
</style>
