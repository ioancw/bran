<script lang="ts">
  import { api, streamChat } from '../lib/api'
  import { router, navigate, href, link } from '../lib/router.svelte'
  import { relativeTime } from '../lib/time'
  import ChatLog from '../chat/ChatLog.svelte'
  import { applyEvent, freshState, type ChatItem, type ReducerState } from '../chat/events'
  import type { Catalog, ChatSummary } from '../lib/types'

  let { sessionId }: { sessionId: string | null } = $props()

  let catalog = $state<Catalog>({ agents: [], commands: [] })
  let chats = $state<ChatSummary[]>([])
  let activeChat = $state<ChatSummary | null>(null)
  // The project whose chats the sidebar shows. A chat is always viewed inside
  // its project — the active chat's project is authoritative; otherwise the
  // ?project= query, else Inbox.
  let scopeProjectId = $state('inbox')
  let projectsById = $state<Record<string, string>>({})
  let items = $state<ChatItem[]>([])
  let streaming = $state(false)
  let streamingIndex = $state(-1)
  let pendingAgent = $state<string | null>(null)
  let input = $state('')
  let showNewForm = $state(false)
  let newAgent = $state('orchestrator')

  let rstate: ReducerState = freshState()
  let loadedId: string | null = null
  let autoSent = false
  let messagesEl: HTMLDivElement | undefined = $state()

  const currentAgent = $derived(activeChat?.agent ?? pendingAgent ?? 'orchestrator')
  const scopeProjectName = $derived(projectsById[scopeProjectId] ?? scopeProjectId)

  function scrollSoon() {
    requestAnimationFrame(() => messagesEl?.scrollTo(0, messagesEl.scrollHeight))
  }

  $effect(() => {
    void api.catalog().then((c) => (catalog = c)).catch(() => {})
    void api.projects().then((ps) => {
      projectsById = Object.fromEntries(ps.map((p) => [p.id, p.name]))
    }).catch(() => {})
  })

  async function refreshChats() {
    try {
      chats = await api.chats(scopeProjectId)
    } catch {
      /* ignore */
    }
  }
  // Reload the sidebar whenever the scope project changes.
  $effect(() => {
    void scopeProjectId
    void refreshChats()
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
          scopeProjectId = c.project_id || 'inbox' // a chat is scoped to its project
        } catch {
          activeChat = null
        }
        await loadHistory(sid)
      })()
    } else {
      activeChat = null
      scopeProjectId = router.route.query.get('project') || 'inbox'
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
    scrollSoon()

    const fields: Record<string, string> = { prompt: text }
    if (activeChat) {
      fields.session_id = activeChat.id
      fields.chat_agent = activeChat.agent
      if (activeChat.project_id) fields.project_id = activeChat.project_id
    } else {
      if (pendingAgent) fields.chat_agent = pendingAgent
      fields.project_id = router.route.query.get('project') || 'inbox'
    }

    try {
      for await (const ev of streamChat(fields)) {
        if (ev.type === 'done') break
        const r = applyEvent(items, rstate, ev)
        if (r.session && !activeChat) {
          const id = r.session
          const proj = router.route.query.get('project') || 'inbox'
          activeChat = {
            id, title: '(new)', agent: pendingAgent ?? 'orchestrator',
            project_id: proj, updated_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
          }
          scopeProjectId = proj
          loadedId = id // pre-set so the route effect won't reload + wipe
          navigate('/chat/' + id)
        }
        streamingIndex = rstate.openAssistant
        scrollSoon()
      }
    } catch (e) {
      items.push({ kind: 'error', message: String(e) })
    } finally {
      streaming = false
      streamingIndex = -1
      void refreshChats()
    }
  }

  function startNewChat() {
    pendingAgent = newAgent
    activeChat = null
    items = []
    rstate = freshState()
    loadedId = null
    showNewForm = false
    navigate('/chat?project=' + encodeURIComponent(scopeProjectId))
  }

  async function deleteChat(id: string, ev: Event) {
    ev.preventDefault()
    ev.stopPropagation()
    if (!confirm('Delete this chat? The SDK transcript stays on disk.')) return
    await api.deleteChat(id)
    if (activeChat?.id === id) navigate('/chat')
    else chats = chats.filter((c) => c.id !== id)
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

<header class="page-header">
  <h1>Chat</h1>
  <span class="subheading">
    {#if scopeProjectId !== 'inbox'}
      in <a href={href('/projects/' + scopeProjectId)} use:link class="text-accent-soft" style="text-decoration: none;">{scopeProjectName}</a>
    {:else}Inbox{/if}
    {#if activeChat} · {activeChat.agent}{/if}
  </span>
  <div class="page-actions">
    <span class="text-muted" style="font-size: 11px; font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.1em;">
      {streaming ? 'thinking…' : ''}
    </span>
  </div>
</header>

<div class="px-8 py-6">
  <div style="display: flex; gap: 16px; height: calc(100vh - 180px);">
    <!-- Chat list (scoped to the current project) -->
    <aside class="card flush" style="width: 260px; flex-shrink: 0; display: flex; flex-direction: column; padding: 0;">
      <div style="padding: 14px;">
        <button class="btn-primary" style="width: 100%;" onclick={() => (showNewForm = !showNewForm)}>+ new chat</button>
        {#if showNewForm}
          <div class="card-quiet" style="margin-top: 10px;">
            <span class="label-cap" style="display: block; margin-bottom: 4px;">Agent</span>
            <select class="field" bind:value={newAgent}>
              {#each catalog.agents as a}<option value={a.name}>{a.name}</option>{/each}
            </select>
            <div style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px;">
              <button class="btn-ghost" onclick={() => (showNewForm = false)}>cancel</button>
              <button class="btn-primary" onclick={startNewChat}>start</button>
            </div>
          </div>
        {/if}
      </div>
      <div style="padding: 0 14px 6px;">
        <div class="label-cap">{scopeProjectName} · conversations</div>
      </div>
      <div style="flex: 1; overflow-y: auto; padding: 0 8px 14px;">
        {#each chats as c}
          <a href={href('/chat/' + encodeURIComponent(c.id))} use:link
             class="chat-row" style="display: block; padding: 8px 10px; border-radius: var(--radius); margin-bottom: 3px; text-decoration: none; background: {activeChat?.id === c.id ? 'var(--accent-glow)' : 'transparent'};">
            <div style="display: flex; align-items: center; gap: 6px;">
              {#if activeChat?.id === c.id && streaming}<span class="live-dot sm"></span>{/if}
              <span style="font-size: 13px; font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: {activeChat?.id === c.id ? 'var(--accent-soft)' : 'var(--fg-bright)'};">{c.title}</span>
              <button onclick={(e) => deleteChat(c.id, e)} title="delete" style="background: transparent; border: 0; color: var(--muted); cursor: pointer;">×</button>
            </div>
            <div style="display: flex; gap: 6px;">
              <span class="font-mono" style="font-size: 10px; color: var(--fg-dim);">{c.agent}</span>
              <span class="ml-auto text-muted font-mono" style="font-size: 9px;">{relativeTime(c.updated_at)}</span>
            </div>
          </a>
        {:else}
          <div class="text-muted" style="padding: 20px 14px; font-size: 12px; text-align: center; font-style: italic;">No conversations in this project yet.</div>
        {/each}
      </div>
    </aside>

    <!-- Message area -->
    <div style="flex: 1; min-width: 0; display: flex; flex-direction: column;">
      <div bind:this={messagesEl} class="space-y-3" style="flex: 1; overflow-y: auto; padding-right: 6px;">
        {#if !items.length}
          <div class="empty-state" style="padding: 80px 24px;">
            <div class="brackets">[       ]</div>
            <h3>{currentAgent !== 'orchestrator' ? `chat with ${currentAgent}` : 'start a conversation'}</h3>
            <p class="cta">type a message below — try <code>/digest</code> or <code>@research</code></p>
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
            <button class="btn-primary" disabled={streaming} onclick={send} style="white-space: nowrap;">Send ↵</button>
          </div>
          <div class="text-muted" style="font-size: 10px; font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.12em; margin-top: 6px;">
            ⏎ send · ⇧⏎ newline · / commands · @ agents · {currentAgent}
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
