<script lang="ts">
  import { onDestroy, tick } from 'svelte'
  import { fly } from 'svelte/transition'
  import { api, streamChat } from '../lib/api'
  import { router, navigate, href, link } from '../lib/router.svelte'
  import {
    workspace,
    setScope,
    loadChats,
    loadProjects,
    projectName,
    bumpActivity,
    setLive,
  } from '../lib/workspace.svelte'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import Composer from '../components/Composer.svelte'
  import ProjectRail from '../components/ProjectRail.svelte'
  import Section from '../components/Section.svelte'
  import SourcesPanel from '../components/SourcesPanel.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import Icon from '../components/Icon.svelte'
  import ChatLog from '../chat/ChatLog.svelte'
  import { applyEvent, freshState, toolDisplayName, type ChatItem, type ReducerState } from '../chat/events'
  import type { Attachment, Catalog, ChatSummary, RunRecord, ScheduleRecord, SourceRef } from '../lib/types'
  import { relativeTime } from '../lib/time'
  import { sourceLabel } from '../lib/labels'

  let { sessionId }: { sessionId: string | null } = $props()

  let catalog = $state<Catalog>({ agents: [], commands: [] })
  let activeChat = $state<ChatSummary | null>(null)
  let items = $state<ChatItem[]>([])
  let streaming = $state(false)
  let streamingIndex = $state(-1)
  let pendingAgent = $state<string | null>(null)
  let input = $state('')
  // Model override for this chat ('' = the agent's own), remembered per chat.
  let model = $state('')
  let modelKeyLoaded: string | null = null
  const modelKey = (sid: string | null) => 'bran.model:' + (sid ?? 'new')
  function readModel(sid: string | null): string {
    try {
      return localStorage.getItem(modelKey(sid)) ?? ''
    } catch {
      return ''
    }
  }
  function writeModel(sid: string | null, v: string) {
    try {
      if (v) localStorage.setItem(modelKey(sid), v)
      else localStorage.removeItem(modelKey(sid))
    } catch {
      /* ignore */
    }
  }
  $effect(() => {
    void model
    // Only persist once the chat's own saved choice has been loaded, so the
    // route change doesn't write the previous chat's model under the new key.
    if (modelKeyLoaded === modelKey(loadedId)) writeModel(loadedId, model)
  })
  let attachments = $state<Attachment[]>([])
  // Sources the conversation cited or read (right rail + citation chips).
  let sources = $state<SourceRef[]>([])
  async function loadSources(sid: string) {
    try {
      const next = await api.chatSources(sid)
      if (activeChat?.id === sid || loadedId === sid) sources = next
    } catch {
      /* sources are a nicety */
    }
  }

  let rstate: ReducerState = freshState()
  let loadedId: string | null = null
  // Bumped each time the route effect takes over the view. A send() started
  // under an older epoch must not touch `items`/`input` after a mid-stream
  // navigation — those now belong to a different conversation.
  let viewEpoch = 0
  let autoSent = false
  let messagesEl: HTMLDivElement | undefined = $state()
  let composer = $state<{ focusInput: () => void } | null>(null)

  // Put the caret in the composer — but never on touch devices, where a
  // programmatic focus pops the keyboard over half the conversation.
  function focusComposer() {
    if (matchMedia('(hover: hover) and (pointer: fine)').matches) composer?.focusInput()
  }

  // --- per-chat drafts -------------------------------------------------------
  // A half-typed message survives navigation and never leaks into another
  // conversation: each chat (and "new") keeps its own draft for the session.
  const draftKey = (sid: string | null) => 'bran.draft:' + (sid ?? 'new')
  function readDraft(sid: string | null): string {
    try {
      return sessionStorage.getItem(draftKey(sid)) ?? ''
    } catch {
      return ''
    }
  }
  $effect(() => {
    const v = input
    try {
      if (v.trim()) sessionStorage.setItem(draftKey(loadedId), v)
      else sessionStorage.removeItem(draftKey(loadedId))
    } catch {
      /* storage unavailable — drafts just don't persist */
    }
  })
  let historyLoading = $state(false)
  // Abort handle for the in-flight stream — the "stop" button's lever.
  let streamAbort: AbortController | null = null

  // Scope = the project whose conversations the rail + sidebar show (null =
  // loose). It lives in the shared workspace store now; a chat is viewed inside
  // its project (the active chat's project is authoritative), else the ?project=
  // query. Initialise synchronously so there's no loose-then-rescope flash.
  setScope(router.route.query.get('project'))
  const scopeId = $derived(workspace.scopeProjectId)
  const scopeName = $derived(projectName(scopeId) ?? 'Recents')

  const currentAgent = $derived(activeChat?.agent ?? pendingAgent ?? 'orchestrator')

  // The activity indicator lives in every silent gap of a turn, not just
  // before the first byte: the model's longest quiet stretch is its *initial*
  // thinking, which arrives only as a completed block. Hide the dots only
  // while something visible is already moving — streaming text, or a tool row
  // with its own pulsing status dot.
  const lastItem = $derived(items.length ? items[items.length - 1] : null)
  const showPending = $derived(
    streaming &&
      streamingIndex < 0 &&
      !(lastItem?.kind === 'tool' && lastItem.status === 'running') &&
      lastItem?.kind !== 'footer',
  )

  // Breadcrumb scope switcher.
  let scopeMenuOpen = $state(false)
  let scopeMenuEl = $state<HTMLElement | undefined>()
  let scopeBtnEl = $state<HTMLButtonElement | undefined>()
  function switchScope(id: string | null) {
    scopeMenuOpen = false
    scopeBtnEl?.focus() // keyboard users land back on the trigger, not in a void
    if (id === scopeId) return // already there — don't abandon the current chat
    // Set the scope directly: the route effect below keys on sessionId, which
    // doesn't change when hopping between fresh chats (/chat?project=A → ?project=B),
    // so navigation alone would update the URL and nothing else.
    setScope(id)
    navigate(id ? '/chat?project=' + encodeURIComponent(id) : '/chat')
  }
  function onWindowClick(e: MouseEvent) {
    if (scopeMenuOpen && scopeMenuEl && !scopeMenuEl.contains(e.target as Node)) {
      scopeMenuOpen = false
    }
  }
  function onWindowKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && scopeMenuOpen) {
      scopeMenuOpen = false
      scopeBtnEl?.focus()
    }
  }
  // Menu keyboard model: opening focuses the current option; arrows move
  // (wrapping), Home/End jump, Enter/Space activate the focused button.
  function menuItems(): HTMLButtonElement[] {
    return Array.from(scopeMenuEl?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])
  }
  $effect(() => {
    if (!scopeMenuOpen) return
    void tick().then(() => {
      const opts = menuItems()
      ;(opts.find((o) => o.classList.contains('on')) ?? opts[0])?.focus()
    })
  })
  function onMenuKeydown(e: KeyboardEvent) {
    const opts = menuItems()
    if (!opts.length) return
    const cur = opts.indexOf(document.activeElement as HTMLButtonElement)
    let next = -1
    if (e.key === 'ArrowDown') next = (cur + 1) % opts.length
    else if (e.key === 'ArrowUp') next = (cur - 1 + opts.length) % opts.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = opts.length - 1
    else return
    e.preventDefault()
    opts[next].focus()
  }

  // Autoscroll only while the reader is pinned to the bottom — scrolling up to
  // re-read during a stream must never be fought. `force` (send, history load)
  // re-pins. When unpinned mid-stream, a "↓ latest" pill offers the way back.
  let pinned = $state(true)
  function onMessagesScroll() {
    const el = messagesEl
    if (!el) return
    pinned = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }
  function scrollSoon(force = false) {
    if (force) pinned = true
    if (!pinned) return
    requestAnimationFrame(() => messagesEl?.scrollTo(0, messagesEl.scrollHeight))
  }
  function jumpToLatest() {
    pinned = true
    messagesEl?.scrollTo({ top: messagesEl.scrollHeight, behavior: 'smooth' })
  }

  // --- Auto fan-in -----------------------------------------------------------
  // The SERVER owns fan-out synthesis (bran.synthesis): when a turn spawns >=2
  // background runs, the server watches the batch and runs the synthesis turn
  // itself — even if this tab is closed. Our only job here is to reflect it:
  // watch for the server's synthesis run in this session and reload the
  // conversation when it lands, so the combined answer appears live.
  // eslint-disable-next-line svelte/prefer-svelte-reactivity -- bookkeeping only, never read by the template
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
  // Guards work that must not run after the component leaves the DOM — a stream
  // in flight when you navigate away would otherwise navigate() you back and
  // keep mutating global state from a dead instance.
  let mounted = true
  function clearFanoutWatchers() {
    fanoutTimers.forEach(clearInterval)
    fanoutTimers = []
  }
  function watchFanout() {
    const watchStart = Date.now() - 60_000 // small skew allowance
    const terminal = (s: string) => s === 'completed' || s === 'failed' || s === 'cancelled'
    let ticks = 0
    const timer = setInterval(async () => {
      // Give up quietly after ~10 min; reopening the chat shows the result anyway.
      if (++ticks > 200) {
        clearInterval(timer)
        fanoutTimers = fanoutTimers.filter((t) => t !== timer)
        return
      }
      if (streaming) return // never clobber an in-flight turn
      const sid = loadedId
      if (!sid) return
      let synth: RunRecord | undefined
      try {
        const runs = await api.runs({ session_id: sid, limit: 20 })
        // Only a synthesis run from THIS fan-out — an older one in the same
        // chat (a previous fan-out) must not trigger a premature reload.
        synth = runs.find(
          (r) => r.source === 'synthesis' && terminal(r.status) && Date.parse(r.started_at) >= watchStart,
        )
      } catch {
        return
      }
      if (!synth) return
      clearInterval(timer)
      fanoutTimers = fanoutTimers.filter((t) => t !== timer)
      await loadHistory(sid) // pull the server-written synthesis turn into view
      bumpActivity()
    }, 3000)
    fanoutTimers.push(timer)
  }
  onDestroy(() => {
    mounted = false
    clearFanoutWatchers()
    streamAbort?.abort() // stop the in-flight turn; the server sees the disconnect
  })

  $effect(() => {
    void api
      .catalog()
      .then((c) => (catalog = c))
      .catch(() => {})
    void loadProjects()
  })

  // React to the active session id (route change). Skip our own URL pivot
  // (loadedId already set).
  $effect(() => {
    const sid = sessionId
    if (sid === loadedId) return
    // Navigating to another chat mid-reply stops the turn (the same lever as
    // the stop button — the server sees the disconnect) and switches. The old
    // behavior silently kept the previous conversation on screen under the
    // new URL until the stream ended.
    if (streaming) streamAbort?.abort()
    viewEpoch++
    loadedId = sid
    sources = []
    clearFanoutWatchers()
    watchedSpawnIds = new Set()
    items = []
    rstate = freshState()
    pendingAgent = null
    // Each conversation gets its own composer contents — a draft typed in the
    // previous chat must not follow us here (it's saved under its own key).
    input = readDraft(sid)
    model = readModel(sid)
    modelKeyLoaded = modelKey(sid)
    attachments = []
    focusComposer()
    if (sid) {
      historyLoading = true // before the first await, so no empty-state flash
      void (async () => {
        try {
          const c = await api.chat(sid)
          if (loadedId !== sid) return // navigated on while awaiting — drop stale result
          activeChat = c
          setScope(c.project_id) // a chat is scoped to its project (null = loose)
        } catch {
          if (loadedId !== sid) return
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
    historyLoading = true
    try {
      void loadSources(sid)
      const { events } = await api.history(sid)
      if (loadedId !== sid) return // a newer navigation owns the view now
      const next: ChatItem[] = []
      const st = freshState()
      for (const ev of events) applyEvent(next, st, ev)
      items = next
      // Seed already-spawned ids so loading a historical fan-out never auto-fires.
      spawnRunIds().forEach((id) => watchedSpawnIds.add(id))
      scrollSoon(true)
    } catch {
      /* ignore */
    } finally {
      historyLoading = false
    }
  }

  // Card actions (PlanCard approve/revise, "Edit" on a user message): approve
  // sends the canned reply immediately; the rest seed the composer for the
  // user to finish.
  // "Pick up where the fleet left off": the newest finished outputs, fetched
  // only while the empty state is on screen.
  let recentOutputs = $state<RunRecord[]>([])
  let recentRunners = $state<ScheduleRecord[]>([])
  let recentLoaded = false
  async function loadRecentOutputs() {
    if (recentLoaded) return
    recentLoaded = true
    try {
      const [runs, runners] = await Promise.all([
        api.runs({ exclude_chats: true, limit: 12 }),
        api.schedules().catch(() => [] as ScheduleRecord[]),
      ])
      recentRunners = runners
      recentOutputs = runs
        .filter((r) => r.status === 'completed' && (r.result ?? '').trim() && r.source !== 'subagent')
        .slice(0, 3)
    } catch {
      /* the block simply stays hidden */
    }
  }
  const outputTitle = (r: RunRecord) =>
    (r.schedule_id && recentRunners.find((s) => s.id === r.schedule_id)?.name) || r.agent
  $effect(() => {
    if (!items.length && !historyLoading) void loadRecentOutputs()
  })

  function onComposerAction(text: string, sendNow: boolean) {
    if (streaming) return
    input = text
    if (sendNow) void send()
    else focusComposer()
  }

  // A failed turn's inputs, kept so the error row's retry can re-send them.
  let lastFailed: { text: string } | null = null
  function retryFailed() {
    if (streaming || !lastFailed) return
    // Drop the error row and its unanswered user bubble — send() re-adds both.
    if (items[items.length - 1]?.kind === 'error') items.pop()
    if (items[items.length - 1]?.kind === 'user') items.pop()
    // The failed text was handed back to the composer; if the user has since
    // cleared it, fall back to what actually failed.
    if (!input.trim()) input = lastFailed.text
    void send()
  }

  async function send() {
    let text = input.trim()
    if (!text || streaming) return
    // Remember the raw input so a failed send can hand it back (see catch) —
    // losing a paragraph-long prompt to a dropped connection is unacceptable.
    const priorInput = input
    const priorAttachments = attachments
    // Fold attachments into the prompt as absolute paths the agent reads on
    // demand — shown in the user bubble too, so what was sent is transparent.
    if (attachments.length) {
      const lines = attachments.map((a) => `- ${a.path}`).join('\n')
      text += `\n\nAttached files (read with Read, or mcp__bran_docs__read_pdf for PDFs):\n${lines}`
      attachments = []
    }
    input = ''
    items.push({ kind: 'user', text, at: new Date().toISOString() })
    streaming = true
    setLive(true, 'thinking…')
    scrollSoon(true)

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
    if (model) fields.model = model

    streamAbort = new AbortController()
    const epoch = viewEpoch
    try {
      for await (const ev of streamChat(fields, streamAbort.signal)) {
        // A read that resolved just as a mid-stream navigation aborted us can
        // still deliver one event — it belongs to the abandoned conversation.
        if (epoch !== viewEpoch) break
        if (ev.type === 'done') break
        // Reflect what the agent is doing into the rail's live Progress row.
        if (ev.type === 'tool_use') setLive(true, `using ${toolDisplayName(ev.name)}`)
        else if (ev.type === 'thinking') setLive(true, 'thinking…')
        else if (ev.type === 'text') setLive(true, 'responding…')
        const r = applyEvent(items, rstate, ev)
        if (r.session && !activeChat) {
          const id = r.session
          const proj = router.route.query.get('project')
          activeChat = {
            id,
            title: '(new)',
            agent: pendingAgent ?? 'orchestrator',
            project_id: proj,
            updated_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
          }
          setScope(proj)
          try {
            localStorage.setItem('bran.didChat', '1')
          } catch {
            /* ignore */
          }
          loadedId = id // pre-set so the route effect won't reload + wipe
          // A model chosen for the new chat belongs to this session now.
          if (model) writeModel(id, model)
          modelKeyLoaded = modelKey(id)
          if (mounted) navigate('/chat/' + id) // don't yank a user who already left
        }
        streamingIndex = rstate.openAssistant
        scrollSoon()
      }
    } catch (e) {
      if (epoch !== viewEpoch) {
        // Mid-stream navigation aborted us — the view (and composer) belong to
        // another conversation now; leave them alone.
      } else if (streamAbort?.signal.aborted) {
        // User pressed stop — a quiet note, not an error.
        items.push({ kind: 'footer', numTurns: null, cost: null, stopped: true })
      } else {
        lastFailed = { text: priorInput }
        items.push({ kind: 'error', message: errorText(e) })
        // Send failed before reaching the server — give the text back so it can
        // be retried instead of retyped.
        input = priorInput
        attachments = priorAttachments
      }
    } finally {
      streamAbort = null
      streaming = false
      streamingIndex = -1
      setLive(false) // global indicator — clear it even if we've unmounted
      if (mounted) {
        void loadChats() // refresh the sidebar Recents
        bumpActivity() // refresh the rail's Progress (new run recorded)
        if (activeChat) void loadSources(activeChat.id)
        // Auto fan-in: if this turn fanned out >=2 new background runs, the
        // server will synthesise when they finish — watch for that synthesis
        // run and refresh the conversation when it lands.
        const fresh = spawnRunIds().filter((id) => !watchedSpawnIds.has(id))
        fresh.forEach((id) => watchedSpawnIds.add(id))
        if (fresh.length >= 2) watchFanout()
      }
    }
  }

  // Stop generating: abort the SSE fetch — the server sees the disconnect and
  // cancels the agent turn. What already streamed in stays in the transcript.
  function stop() {
    streamAbort?.abort()
  }
</script>

<!-- Close the scope menu on outside click / Escape. -->
<svelte:window onclick={onWindowClick} onkeydown={onWindowKeydown} />

<Page fill={true}>
  {#snippet head()}
    <!-- Breadcrumb: <scope ▾> / conversation -->
    <div class="bc">
      <span class="crumb-wrap" bind:this={scopeMenuEl}>
        <button
          class="crumb-scope"
          bind:this={scopeBtnEl}
          aria-haspopup="menu"
          aria-expanded={scopeMenuOpen}
          onclick={() => (scopeMenuOpen = !scopeMenuOpen)}
        >
          {scopeName}
          <Icon name="chevron-down" size={14} />
        </button>
        {#if scopeMenuOpen}
          <div
            class="crumb-menu card elev-md"
            role="menu"
            tabindex="-1"
            aria-label="Conversation scope"
            transition:fly={{ y: -4, duration: 120 }}
            onkeydown={onMenuKeydown}
          >
            <button
              class="crumb-opt"
              class:on={!scopeId}
              role="menuitem"
              tabindex="-1"
              onclick={() => switchScope(null)}>Recents (all chats)</button
            >
            {#each workspace.projects as p (p.id)}
              <button
                class="crumb-opt"
                class:on={scopeId === p.id}
                role="menuitem"
                tabindex="-1"
                onclick={() => switchScope(p.id)}>{p.name}</button
              >
            {/each}
          </div>
        {/if}
      </span>
      <span class="crumb-sep">/</span>
      <span class="crumb-title">{activeChat?.title ?? 'New chat'}</span>
      {#if activeChat}<span class="pill mute crumb-agent">{activeChat.agent}</span>{/if}
    </div>
    {#if streaming}<span class="ml-auto thinking"><span class="live-dot sm"></span>Thinking…</span>{/if}
  {/snippet}

  <div style="display: flex; gap: 16px; flex: 1; min-height: 0;">
    <!-- Center: conversation + composer -->
    <div style="flex: 1; min-width: 0; display: flex; flex-direction: column; position: relative;">
      <div
        bind:this={messagesEl}
        class="space-y-3 chat-scroll"
        class:chat-empty={!items.length}
        onscroll={onMessagesScroll}
        style="flex: 1; overflow-y: auto; padding-right: 6px;"
      >
        {#if historyLoading && !items.length}
          <Skeleton rows={6} />
        {:else if !items.length}
          <div class="empty-state chat-welcome">
            <svg class="welcome-crow" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path
                d="m10 18l-1.65 4l-1.85-.75l1.45-3.525q-2.65-.7-4.3-2.85T2 10V6q0-1.65 1.175-2.825T6 2q.55 0 1.05.187t1 .388L14 5l-4 1.5V8l7.85 5H10q-.825 0-1.412-.587T8 11H6q0 1.65 1.175 2.825T10 15h11l1 5h-2l-1-2h-5v4h-2v-4zM5.288 5.288Q5 5.575 5 6t.288.713T6 7t.713-.288T7 6t-.288-.712T6 5t-.712.288"
              />
            </svg>
            <h3 class="welcome-title">
              {currentAgent !== 'orchestrator' ? `Chat with ${currentAgent}` : 'What can bran do for you?'}
            </h3>
            <p class="cta">Ask anything, or try <code>/digest</code> or <code>@research</code></p>
            <div class="starters">
              <button
                class="btn-outline"
                onclick={() => {
                  input = 'What did my runners deliver today?'
                  focusComposer()
                }}>What did my runners deliver today?</button
              >
              <button
                class="btn-outline"
                onclick={() => {
                  input = 'Summarise the latest finance briefing for me.'
                  focusComposer()
                }}>Summarise the latest briefing</button
              >
              <button
                class="btn-outline"
                onclick={() => {
                  input = '/digest '
                  focusComposer()
                }}>Digest a topic</button
              >
            </div>
            {#if recentOutputs.length}
              <div class="pickup">
                <div class="label-cap pickup-label">Pick up where the fleet left off</div>
                {#each recentOutputs as r (r.id)}
                  <div class="pickup-row">
                    <a href={href('/runs/' + r.id)} use:link class="pickup-title">{outputTitle(r)}</a>
                    <span class="src src-{r.source}">{sourceLabel(r.source)}</span>
                    <span class="pickup-time">{relativeTime(r.started_at)}</span>
                    <button
                      class="btn-text"
                      onclick={() =>
                        onComposerAction(
                          `Let's discuss the output of run ${r.id} (${outputTitle(r)}).`,
                          false,
                        )}>Discuss</button
                    >
                  </div>
                {/each}
              </div>
            {/if}
          </div>
        {:else}
          <ChatLog {items} {streamingIndex} {sources} onaction={onComposerAction} onretry={retryFailed} />
          {#if showPending}
            <div class="pending-turn" aria-label="working on a reply">
              <span class="pending-dot"></span><span class="pending-dot"></span><span class="pending-dot"
              ></span>
              <span class="pending-label">{workspace.liveLabel || 'thinking…'}</span>
            </div>
          {/if}
        {/if}
      </div>

      <!-- Screen-reader narration of stream state; the token stream itself is
           too chatty to announce. -->
      <div class="sr-only" role="status" aria-live="polite">
        {streaming ? 'bran is responding' : items.length ? 'response complete' : ''}
      </div>

      {#if !pinned && streaming}
        <!-- Only while a reply is streaming — an idle reader scrolling old
             messages doesn't need chrome floating over the page. -->
        <div class="jump-wrap">
          <button
            class="btn-outline jump-latest elev-md"
            transition:fly={{ y: 6, duration: 160 }}
            onclick={jumpToLatest}
            aria-label="Jump to latest"
            title="Jump to latest"
          >
            <Icon name="chevron-down" size={16} />
          </button>
        </div>
      {/if}

      <!-- Composer -->
      <div class="composer-col">
        <Composer
          bind:this={composer}
          bind:value={input}
          bind:attachments
          bind:model
          attach={true}
          {catalog}
          hint="Enter to send · Shift+Enter for a new line"
          busy={streaming}
          placeholder="Message bran…"
          onsubmit={send}
          onstop={stop}
        >
          {#snippet leading()}
            {#if activeChat}
              <span class="composer-agent agent-fixed"><Icon name="bot" size={13} />{activeChat.agent}</span>
            {:else}
              <label class="agent-pick btn-ghost btn-sm" title="Agent for this chat">
                <Icon name="bot" size={13} />
                <span>{pendingAgent ?? 'orchestrator'}</span>
                <Icon name="chevron-down" size={12} />
                <select class="agent-select" bind:value={pendingAgent} aria-label="Agent for this chat">
                  <option value={null}>orchestrator</option>
                  {#each catalog.agents.filter((a) => a.name !== 'orchestrator') as a (a.name)}<option
                      value={a.name}>{a.name}</option
                    >{/each}
                </select>
              </label>
            {/if}
          {/snippet}
        </Composer>
      </div>
    </div>

    <!-- Right rail: project context (Cowork-style), only when in a project. -->
    {#if scopeId || sources.length}
      <aside class="chat-rail">
        {#if scopeId}
          <div style="margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px;">
            <a href={href('/projects/' + encodeURIComponent(scopeId))} use:link class="rail-title"
              >{scopeName}</a
            >
            <span class="label-cap">Project</span>
          </div>
          <ProjectRail projectId={scopeId} mode="chat" sessionId={activeChat?.id ?? null} />
        {/if}
        {#if sources.length}
          <div class="rail-sources">
            <Section label="Sources">
              <SourcesPanel {sources} />
            </Section>
          </div>
        {/if}
      </aside>
    {/if}
  </div>
</Page>

<style>
  .rail-sources {
    margin-top: 10px;
  }
  /* The conversation is a reading column: every turn (and the composer below)
     caps at --chat-col and centres. On narrow panes the cap is inert. */
  .chat-scroll > :global(*) {
    max-width: var(--chat-col);
    margin-left: auto;
    margin-right: auto;
  }
  .composer-col {
    margin: 14px auto 4px;
    width: 100%;
    max-width: var(--chat-col);
    padding-bottom: var(--safe-bottom);
  }

  /* New/empty chat: centre the invitation vertically (the composer stays
     pinned at the bottom). */
  .chat-empty {
    display: flex;
    flex-direction: column;
    justify-content: center;
  }
  .chat-welcome {
    max-width: 560px;
  }
  .welcome-crow {
    width: 36px;
    height: 36px;
    color: var(--accent);
    margin-bottom: 14px;
  }
  .welcome-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--fg-bright);
    margin-bottom: 6px;
  }
  .starters {
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 20px;
  }
  .pickup {
    margin: 28px auto 0;
    max-width: 520px;
    text-align: left;
  }
  .pickup-label {
    display: block;
    margin-bottom: 6px;
  }
  .pickup-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid var(--border);
    font-size: 13px;
  }
  .pickup-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 500;
    color: var(--fg-bright);
  }
  .pickup-time {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
  @media (max-width: 768px) {
    .composer-col {
      padding-right: 6px;
    }
  }

  /* Agent picker in the composer footer. A fixed label once the chat exists;
     a select dressed as a quiet button before the first message. */
  .agent-fixed {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .agent-pick {
    position: relative;
    cursor: pointer;
  }
  .agent-select {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
    width: 100%;
  }

  /* Breadcrumb bar. */
  .bc {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    font-size: 14px;
  }
  .crumb-wrap {
    position: relative;
    display: inline-flex;
  }
  .crumb-scope {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 28px;
    padding: 0 6px 0 8px;
    margin-left: -8px;
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius);
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-dim);
    white-space: nowrap;
  }
  .crumb-scope:hover,
  .crumb-scope[aria-expanded='true'] {
    background: var(--surface2);
    color: var(--fg-bright);
  }
  .crumb-sep {
    color: var(--border2);
  }
  .crumb-title {
    color: var(--fg-bright);
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .crumb-agent {
    font-size: 11px;
    flex-shrink: 0;
  }
  .thinking {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
  .crumb-menu {
    position: absolute;
    top: 100%;
    left: 0;
    margin-top: 6px;
    padding: 4px;
    min-width: 220px;
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
  }
  .crumb-opt:hover,
  .crumb-opt:focus-visible {
    background: var(--surface2);
    outline: none;
  }
  .crumb-opt.on {
    color: var(--accent-soft);
    font-weight: 500;
  }

  .chat-rail {
    width: 320px;
    flex-shrink: 0;
    overflow-y: auto;
    padding-left: 4px;
  }
  .rail-title {
    font-weight: 600;
    color: var(--fg-bright);
  }
  .rail-title:hover {
    text-decoration: none;
    color: var(--accent-soft);
  }

  /* Floating "back to the live edge" button — appears when the reader scrolls
     up during a stream, sits just above the composer. */
  .jump-wrap {
    position: absolute;
    bottom: 104px;
    left: 0;
    right: 0;
    display: flex;
    justify-content: center;
    pointer-events: none;
    z-index: 5;
  }
  .jump-latest {
    pointer-events: auto;
    width: 34px;
    height: 34px;
    padding: 0;
    border-radius: 50%;
  }

  /* Visually hidden, still announced. */
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }

  /* Instant feedback between hitting send and the first streamed token. */
  .pending-turn {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 10px 4px;
  }
  .pending-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    animation: pending-bounce 1.1s ease-in-out infinite;
  }
  .pending-dot:nth-child(2) {
    animation-delay: 0.18s;
  }
  .pending-dot:nth-child(3) {
    animation-delay: 0.36s;
  }
  .pending-label {
    margin-left: 6px;
    font-size: 12px;
    color: var(--muted);
  }
  @keyframes pending-bounce {
    0%,
    100% {
      opacity: 0.25;
    }
    50% {
      opacity: 1;
    }
  }
</style>
