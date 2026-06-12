<script lang="ts">
  import { onDestroy } from 'svelte'
  import { api, streamChat } from '../lib/api'
  import { router, navigate, href, link } from '../lib/router.svelte'
  import { workspace, setScope, loadChats, loadProjects, projectName, bumpActivity, setLive } from '../lib/workspace.svelte'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import Composer from '../components/Composer.svelte'
  import ProjectRail from '../components/ProjectRail.svelte'
  import ChatLog from '../chat/ChatLog.svelte'
  import { applyEvent, freshState, type ChatItem, type ReducerState } from '../chat/events'
  import type { Attachment, Catalog, ChatSummary, RunRecord } from '../lib/types'

  let { sessionId }: { sessionId: string | null } = $props()

  let catalog = $state<Catalog>({ agents: [], commands: [] })
  let activeChat = $state<ChatSummary | null>(null)
  let items = $state<ChatItem[]>([])
  let streaming = $state(false)
  let streamingIndex = $state(-1)
  let pendingAgent = $state<string | null>(null)
  let input = $state('')
  let attachments = $state<Attachment[]>([])

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

  // --- Auto fan-in -----------------------------------------------------------
  // When a turn fans out >=2 background runs (spawn_agent), watch them; once
  // they ALL finish, automatically ask the agent to collect + synthesise their
  // results — so you get one combined answer without prompting again. Works
  // while you're viewing the chat (a chat turn has no server-side callback).
  let watchedSpawnIds = new Set<string>()
  let fanoutTimers: ReturnType<typeof setInterval>[] = []

  function spawnRunIds(): string[] {
    const ids: string[] = []
    for (const it of items) {
      if (it.kind === 'tool' && it.name === 'mcp__bran__spawn_agent') {
        const m = it.resultText?.match(/run_id=([0-9a-f-]{8,})/i)
        if (m) ids.push(m[1])
      }
    }
    return ids
  }
  function clearFanoutWatchers() {
    fanoutTimers.forEach(clearInterval)
    fanoutTimers = []
  }
  function watchFanout(ids: string[]) {
    const timer = setInterval(async () => {
      let runs: (RunRecord | null)[]
      try {
        runs = await Promise.all(ids.map((id) => api.run(id).catch(() => null)))
      } catch {
        return
      }
      const known = runs.filter((r): r is RunRecord => !!r)
      if (known.length < ids.length) return
      const terminal = (s: string) => s === 'completed' || s === 'failed' || s === 'cancelled'
      if (!known.every((r) => terminal(r.status))) return
      // All finished — but wait until the chat is idle and the user isn't typing,
      // so we don't interrupt a turn or clobber a draft.
      if (streaming || input.trim()) return
      clearInterval(timer)
      fanoutTimers = fanoutTimers.filter((t) => t !== timer)
      if (known.some((r) => r.status === 'completed')) {
        input = 'Those background runs have finished — collect their results and synthesise them into one combined summary for me.'
        void send()
      }
    }, 3000)
    fanoutTimers.push(timer)
  }
  onDestroy(clearFanoutWatchers)

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
    clearFanoutWatchers()
    watchedSpawnIds = new Set()
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

  // ?draft=… pre-fills the composer WITHOUT sending (e.g. the "discuss" action
  // on an Output card) — the user finishes the thought before it goes out.
  let drafted = false
  $effect(() => {
    const d = router.route.query.get('draft')
    if (d && !sessionId && !drafted && !input) {
      drafted = true
      input = d
    }
  })

  async function loadHistory(sid: string) {
    try {
      const { events } = await api.history(sid)
      const next: ChatItem[] = []
      const st = freshState()
      for (const ev of events) applyEvent(next, st, ev)
      items = next
      // Seed already-spawned ids so loading a historical fan-out never auto-fires.
      spawnRunIds().forEach((id) => watchedSpawnIds.add(id))
      scrollSoon()
    } catch {
      /* ignore */
    }
  }

  // PlanCard approve/revise: approve sends the canned reply immediately;
  // revise just seeds the composer for the user to finish.
  function onPlanAction(text: string, sendNow: boolean) {
    if (streaming) return
    input = text
    if (sendNow) void send()
  }

  async function send() {
    let text = input.trim()
    if (!text || streaming) return
    // Fold attachments into the prompt as absolute paths the agent reads on
    // demand — shown in the user bubble too, so what was sent is transparent.
    if (attachments.length) {
      const lines = attachments.map((a) => `- ${a.path}`).join('\n')
      text += `\n\nAttached files (read with Read, or mcp__bran_docs__read_pdf for PDFs):\n${lines}`
      attachments = []
    }
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
      // Auto fan-in: if this turn fanned out >=2 new background runs, watch them
      // and synthesise automatically once they all finish.
      const fresh = spawnRunIds().filter((id) => !watchedSpawnIds.has(id))
      fresh.forEach((id) => watchedSpawnIds.add(id))
      if (fresh.length >= 2) watchFanout(fresh)
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
      <div bind:this={messagesEl} class="space-y-3" class:chat-empty={!items.length}
           style="flex: 1; overflow-y: auto; padding-right: 6px;">
        {#if !items.length}
          <div class="empty-state">
            <div class="brackets">[       ]</div>
            <h3>{currentAgent !== 'orchestrator' ? `chat with ${currentAgent}` : 'start a conversation'}</h3>
            <p class="cta">type a message below — try <code>/digest</code> or <code>@research</code></p>
          </div>
        {:else}
          <ChatLog {items} {streamingIndex} onaction={onPlanAction} />
        {/if}
      </div>

      <!-- Composer -->
      <div style="margin-top: 14px;">
        <Composer bind:value={input} bind:attachments attach={true} {catalog}
                  hint="⏎ send · ⇧⏎ newline · / @"
                  busy={streaming} placeholder="Message bran…" onsubmit={send}>
          {#snippet leading()}
            {#if activeChat}
              <span class="composer-agent">{activeChat.agent}</span>
            {:else}
              <select class="composer-agent-select" bind:value={pendingAgent} title="agent for this chat">
                <option value={null}>orchestrator</option>
                {#each catalog.agents.filter((a) => a.name !== 'orchestrator') as a}<option value={a.name}>{a.name}</option>{/each}
              </select>
            {/if}
          {/snippet}
        </Composer>
      </div>
    </div>

    <!-- Right rail: project context (Cowork-style), only when in a project. -->
    {#if scopeId}
      <aside class="chat-rail">
        <div style="margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px;">
          <a href={href('/projects/' + encodeURIComponent(scopeId))} use:link class="text-bright" style="font-weight: 600; text-decoration: none;">{scopeName}</a>
          <span class="label-cap">project</span>
        </div>
        <ProjectRail projectId={scopeId} mode="chat" />
      </aside>
    {/if}
  </div>
</Page>

<style>
  /* New/empty chat: centre the invitation vertically so it doesn't sit top-heavy
     above a void (the composer stays pinned at the bottom). */
  .chat-empty {
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  /* Agent picker living in the composer footer (new chats only) — styled to read
     like the inline agent label, with a dropdown affordance. */
  .composer-agent-select {
    background: transparent;
    border: 0;
    padding: 0;
    cursor: pointer;
    outline: none;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--accent-soft);
  }

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
  /* .composer / .composer-* now live in global.css (shared input component). */
</style>
