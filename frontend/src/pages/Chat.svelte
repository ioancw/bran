<script lang="ts">
  import { onDestroy, tick } from 'svelte'
  import { fly } from 'svelte/transition'
  import { api, streamChat } from '../lib/api'
  import { router, navigate, replace, href, link } from '../lib/router.svelte'
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
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Icon from '../components/Icon.svelte'
  import ChatLog from '../chat/ChatLog.svelte'
  import DocPanel from '../components/DocPanel.svelte'
  import TeammateRail from '../components/TeammateRail.svelte'
  import { savedDocName } from '../chat/DocCard.svelte'
  import { toast } from '../lib/toast.svelte'
  import {
    applyEvent,
    freshState,
    isWork,
    toolDisplayName,
    DOC_TOOL,
    type ChatItem,
    type ReducerState,
  } from '../chat/events'
  import {
    beginTurn,
    endTurnStopped,
    failTurn,
    itemsFromHistory,
    lastAnswer,
    lastPrompt,
    readDraft,
    spawnRunIds,
    takeRetry,
    writeDraft,
  } from '../chat/turn'
  import type { Attachment, Catalog, ChatSummary, RunRecord, ScheduleRecord, SourceRef } from '../lib/types'
  import { relativeTime } from '../lib/time'
  import { sourceLabel } from '../lib/labels'
  import { isSuperseded } from '../lib/verification'

  let { sessionId }: { sessionId: string | null } = $props()

  let catalog = $state<Catalog>({ agents: [], commands: [] })
  let activeChat = $state<ChatSummary | null>(null)
  let items = $state<ChatItem[]>([])
  let streaming = $state(false)
  let streamingIndex = $state(-1)
  let pendingAgent = $state<string | null>(null)
  // Set when this is a teammate's chat (its one chat): the header, the rail
  // and every message sent follow from it.
  let mate = $state<{ id: string; name: string } | null>(null)
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
    if (loadedId !== undefined && modelKeyLoaded === modelKey(loadedId)) writeModel(loadedId, model)
  })
  let attachments = $state<Attachment[]>([])
  // Sources the conversation cited or read (right rail + citation chips).
  let sources = $state<SourceRef[]>([])
  let sourcesOpen = $state(false) // the narrow-window sheet (rail hidden ≤1100px)
  async function loadSources(sid: string) {
    try {
      const next = await api.chatSources(sid)
      if (activeChat?.id === sid || loadedId === sid) sources = next
    } catch {
      /* sources are a nicety */
    }
  }

  // Suggested next questions under the newest answer (one small model call
  // after each completed turn; [] when the server has them switched off).
  let followups = $state<string[]>([])
  let followSeq = 0
  // Thumbs up/down given in this chat: answer key -> 1 / -1.
  let feedback = $state<Record<string, number>>({})
  async function loadFeedback(sid: string) {
    try {
      const next = await api.chatFeedback(sid)
      if (loadedId === sid) feedback = next
    } catch {
      /* ratings are a nicety */
    }
  }
  async function rateAnswer(key: string, rating: number, prompt: string, answer: string) {
    const chat = activeChat
    if (!chat) return
    const before = feedback
    const next = { ...feedback }
    if (rating) next[key] = rating
    else delete next[key]
    feedback = next
    try {
      await api.rateAnswer(chat.id, { key, rating, prompt, answer, agent: chat.agent })
    } catch (e) {
      feedback = before
      toast(errorText(e), 'err')
    }
  }

  // A document open beside the conversation (a report the agent saved, or a
  // project file). The chat stays live next to it.
  let doc = $state<{ name: string; title: string; url: string } | null>(null)
  function docUrl(name: string): string {
    const pid = activeChat ? activeChat.project_id : router.route.query.get('project')
    return pid
      ? `/spa/projects/${encodeURIComponent(pid)}/files/${encodeURIComponent(name)}`
      : `/spa/documents/${encodeURIComponent(name)}`
  }
  function openDoc(name: string, title = '') {
    doc = { name, title, url: docUrl(name) }
  }
  // How many times this conversation has saved the open document: when the
  // agent saves it again the panel reloads.
  const docRev = $derived.by(() => {
    const d = doc
    if (!d) return 0
    return items.filter((it) => it.kind === 'tool' && it.name === DOC_TOOL && savedDocName(it) === d.name)
      .length
  })
  function askAboutDoc() {
    if (!doc) return
    if (!input.trim()) input = `In ${doc.name}: `
    focusComposer()
  }

  let rstate: ReducerState = freshState()
  // The conversation the view currently holds: a session id, null for a new
  // chat, and `undefined` until the route effect has synced for the first
  // time. (It used to start as null, so a fresh /chat never ran the route
  // effect: the saved draft was not restored and was then wiped.)
  let loadedId: string | null | undefined = undefined
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

  // --- per-chat drafts (see chat/turn.ts) -------------------------------------
  $effect(() => {
    const v = input
    // Nothing is persisted before the first route sync: until then `input` is
    // just the empty initial value, and writing it would erase the draft the
    // sync is about to restore.
    if (loadedId !== undefined) writeDraft(loadedId, v)
  })
  let historyLoading = $state(false)
  // Set when the conversation could not be loaded. The view must not look
  // like an empty new chat then — sending would silently start a different
  // conversation.
  let loadError = $state<unknown>(null)
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
  // An activity row (thinking / tool calls) is its own live indicator, so the
  // dots only fill the gaps it cannot: before the first event, and after a
  // card (a spawn, a delegation, a saved document).
  const showPending = $derived(
    streaming && streamingIndex < 0 && lastItem?.kind !== 'footer' && !(lastItem && isWork(lastItem)),
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
      // Pull the server-written synthesis turn into view — unless a turn
      // started while we were fetching (checked again inside, after the await).
      await loadHistory(sid, true)
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
    loadError = null
    sources = []
    followups = []
    followSeq++
    feedback = {}
    doc = null
    clearFanoutWatchers()
    watchedSpawnIds = new Set()
    items = []
    rstate = freshState()
    pendingAgent = null
    mate = null
    // Each conversation gets its own composer contents — a draft typed in the
    // previous chat must not follow us here (it's saved under its own key).
    input = readDraft(sid)
    model = readModel(sid)
    modelKeyLoaded = modelKey(sid)
    attachments = []
    focusComposer()
    if (sid) void openChat(sid)
    else activeChat = null
  })

  // Load a conversation into the view: its metadata, then its history.
  async function openChat(sid: string) {
    loadError = null
    historyLoading = true // before the first await, so no empty-state flash
    try {
      const c = await api.chat(sid)
      if (loadedId !== sid) return // navigated on while awaiting — drop stale result
      activeChat = c
      mate = c.teammate ?? null
      setScope(c.project_id) // a chat is scoped to its project (null = loose)
      void loadFeedback(sid)
    } catch (e) {
      if (loadedId !== sid) return
      activeChat = null
      loadError = e // a deleted chat, or the server is unreachable
      historyLoading = false
      return
    }
    await loadHistory(sid)
  }

  // A new chat's scope follows ?project= — also when only the query changes
  // (the palette's "New chat in <project>" from another new chat), which the
  // effect above cannot see because the session id stays null.
  $effect(() => {
    const proj = router.route.query.get('project')
    if (!sessionId && !activeChat) setScope(proj)
  })

  // A new chat addressed to a teammate (?teammate=…): if it already has its
  // chat, go there — it has only the one; otherwise this becomes it.
  $effect(() => {
    const id = router.route.query.get('teammate')
    if (sessionId) return
    if (!id) {
      if (!activeChat) mate = null
      return
    }
    let live = true
    void api
      .teammate(id)
      .then((t) => {
        if (!live) return
        if (t.chat_id) {
          replace('/chat/' + encodeURIComponent(t.chat_id))
          return
        }
        mate = { id: t.id, name: t.name }
        pendingAgent = t.agent
      })
      .catch(() => {
        /* an unknown teammate: this is just a new chat */
      })
    return () => {
      live = false
    }
  })

  function reloadChat() {
    if (sessionId && loadedId === sessionId) void openChat(sessionId)
  }

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
      // Consume the parameter: left in the URL it stays in history, and coming
      // Back to it (or reloading) would send the prompt again — a duplicate
      // chat and real spend.
      const qs = [...router.route.query]
        .filter(([k]) => k !== 'prompt')
        .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v))
        .join('&')
      replace('/chat' + (qs ? '?' + qs : ''))
      void send(p)
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

  // `background` = a refresh nobody asked for (the fan-out watcher): it must
  // never replace the view under a turn that started meanwhile, and a failure
  // is not worth an error banner.
  async function loadHistory(sid: string, background = false) {
    if (!background) historyLoading = true
    try {
      void loadSources(sid)
      const { events } = await api.history(sid)
      if (loadedId !== sid) return // a newer navigation owns the view now
      if (background && streaming) return // a live turn owns `items` now
      const replay = itemsFromHistory(events)
      items = replay.items
      rstate = replay.state // indexes into the old array mean nothing now
      // Seed already-spawned ids so loading a historical fan-out never auto-fires.
      spawnRunIds(items).forEach((id) => watchedSpawnIds.add(id))
      scrollSoon(true)
    } catch (e) {
      if (!background && loadedId === sid) loadError = e
    } finally {
      if (!background) historyLoading = false
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
        // A workflow alone records dozens of child runs, so look well past
        // the newest handful to find finished reports.
        api.runs({ exclude_chats: true, exclude_subagents: true, limit: 40 }),
        api.schedules().catch(() => [] as ScheduleRecord[]),
      ])
      recentRunners = runners
      recentOutputs = runs
        .filter(
          (r) =>
            r.status === 'completed' &&
            (r.result ?? '').trim() &&
            r.source !== 'subagent' &&
            !isSuperseded(r),
        )
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

  // The home screen: a brand-new chat with nothing in it. The composer sits in
  // the middle of the page with the question above it, and below it where the
  // chat will live (a project), a few things to ask, and recent work.
  const isHome = $derived(!sessionId && !items.length && !historyLoading && !loadError)
  // How a new chat is handled: "Ask" is the orchestrator (it delegates when
  // that helps), "Research" the research agent, or any one agent directly.
  const hasResearch = $derived(catalog.agents.some((a) => a.name === 'research'))
  const otherAgents = $derived(
    catalog.agents.filter((a) => a.name !== 'orchestrator' && a.name !== 'research'),
  )
  // Things to ask, from what is actually here — the newest runner report first.
  const suggestions = $derived.by(() => {
    const out: { label: string; text: string }[] = []
    const report = recentOutputs.find((r) => r.schedule_id)
    if (report) {
      out.push({
        label: `What matters most in the latest ${outputTitle(report)}?`,
        text: `Read the output of run ${report.id} (${outputTitle(report)}) and tell me the three things that matter most.`,
      })
    }
    out.push({
      label: 'What did my automations deliver today?',
      text: 'What did my automations deliver today?',
    })
    out.push({ label: 'Digest a topic', text: '/digest ' })
    return out
  })

  function onComposerAction(text: string, sendNow: boolean) {
    if (streaming) return
    input = text
    if (sendNow) void send()
    else focusComposer()
  }

  // What a failed send handed back to the composer (raw text + attachments),
  // so a Retry can tell "still the failed prompt" from a new draft.
  let handedBack: string | null = null
  // Retry re-sends the prompt that failed — whatever failed (the network, or
  // an error the server reported) and even after a reload (see takeRetry).
  function retryFailed() {
    if (streaming) return
    const text = takeRetry(items)
    if (text == null) return
    // The transcript's copy already carries the attachment lines; drop the
    // handed-back composer contents so they are not sent a second time.
    if (handedBack != null && input === handedBack) {
      input = ''
      attachments = []
    }
    handedBack = null
    void send(text)
  }

  // Ask the last question again, as a new turn (the earlier answer stays).
  function askAgain() {
    if (streaming) return
    const text = lastPrompt(items)
    if (text) void send(text)
  }
  // Hand a prompt to the runner form's "describe it in a sentence" box.
  function makeRunner(prompt: string) {
    navigate('/runners?draft=' + encodeURIComponent(prompt.slice(0, 800)))
  }
  async function loadFollowups() {
    const chat = activeChat
    const question = lastPrompt(items)
    const answer = lastAnswer(items)
    if (!chat || !question || !answer) return
    const seq = ++followSeq
    try {
      const next = await api.followups(question, answer)
      // Not for a conversation, or a turn, that has since moved on.
      if (seq !== followSeq || loadedId !== chat.id || streaming) return
      followups = next
      scrollSoon()
    } catch {
      /* suggestions are a nicety */
    }
  }

  // `override` sends that exact text (a retry, the ?prompt= launcher) and
  // leaves the composer's own draft alone.
  async function send(override?: string) {
    if (streaming || loadError) return
    const fromComposer = override === undefined
    let text = (fromComposer ? input : override).trim()
    if (!text) return
    // Remember the raw input so a failed send can hand it back (see catch) —
    // losing a paragraph-long prompt to a dropped connection is unacceptable.
    const priorInput = fromComposer ? input : override
    const priorAttachments = fromComposer ? attachments : []
    // Fold attachments into the prompt as absolute paths the agent reads on
    // demand — shown in the user bubble too, so what was sent is transparent.
    if (fromComposer && attachments.length) {
      const lines = attachments.map((a) => `- ${a.path}`).join('\n')
      text += `\n\nAttached files (read with Read, or mcp__bran_docs__read_pdf for PDFs):\n${lines}`
      attachments = []
    }
    if (fromComposer) input = ''
    handedBack = null
    followups = []
    followSeq++
    let completed = false
    beginTurn(items, rstate, text)
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
    if (mate) fields.teammate_id = mate.id

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
        if (ev.type === 'error') completed = false
        // The turn's closing line says how long it took: stamp the moment.
        const r = applyEvent(
          items,
          rstate,
          ev.type === 'result' ? { ...ev, ts: new Date().toISOString() } : ev,
        )
        if (ev.type === 'result') completed = true
        if (r.session && !activeChat) {
          const id = r.session
          const proj = router.route.query.get('project')
          activeChat = {
            id,
            title: mate ? mate.name : '(new)',
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
        endTurnStopped(items, rstate)
      } else {
        failTurn(items, rstate, errorText(e))
        // Send failed before reaching the server — give the text back so it can
        // be retried instead of retyped (unless the composer has moved on).
        if (!input.trim() && !attachments.length) {
          input = priorInput
          attachments = priorAttachments
          handedBack = priorInput
        }
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
        if (completed && epoch === viewEpoch) void loadFollowups()
        // Auto fan-in: if this turn fanned out >=2 new background runs, the
        // server will synthesise when they finish — watch for that synthesis
        // run and refresh the conversation when it lands.
        const fresh = spawnRunIds(items).filter((id) => !watchedSpawnIds.has(id))
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
      {#if mate}
        <a href={href('/teammates')} use:link class="crumb-scope crumb-link">Teammates</a>
      {:else}
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
      {/if}
      <span class="crumb-sep">/</span>
      <span class="crumb-title">{mate ? mate.name : (activeChat?.title ?? 'New chat')}</span>
      {#if activeChat}<span class="pill mute crumb-agent">{activeChat.agent}</span>{/if}
    </div>
    {#if streaming}<span class="ml-auto thinking"><span class="live-dot sm"></span>Thinking…</span>{/if}
    {#if sources.length}
      <!-- Narrow windows hide the right rail; the sources stay one click away. -->
      <button
        class="btn-ghost btn-sm sources-btn"
        class:ml-auto={!streaming}
        aria-expanded={sourcesOpen}
        onclick={() => (sourcesOpen = !sourcesOpen)}
        title="Where this conversation's answers came from"
      >
        <Icon name="link" size={13} />Sources <span class="text-muted">{sources.length}</span>
      </button>
    {/if}
  {/snippet}

  {#if sourcesOpen && sources.length}
    <div class="sources-sheet card elev-md">
      <div class="sources-sheet-head">
        <span class="label-cap">Sources</span>
        <button class="btn-icon ml-auto" aria-label="Close sources" onclick={() => (sourcesOpen = false)}>
          <Icon name="x" size={14} />
        </button>
      </div>
      <SourcesPanel {sources} />
    </div>
  {/if}

  <div style="display: flex; gap: 16px; flex: 1; min-height: 0;">
    <!-- Center: conversation + composer -->
    <div
      class="chat-main"
      class:home={isHome}
      style="flex: 1; min-width: 0; display: flex; flex-direction: column; position: relative;"
    >
      <div
        bind:this={messagesEl}
        class="space-y-3 chat-scroll"
        class:chat-empty={!items.length}
        onscroll={onMessagesScroll}
        style={isHome ? 'padding-right: 6px;' : 'flex: 1; overflow-y: auto; padding-right: 6px;'}
      >
        {#if loadError}
          <div class="load-error">
            <ErrorBanner error={loadError} onretry={reloadChat} />
            <p class="text-muted">
              This conversation could not be loaded, so nothing can be sent to it.
              <a href={href('/chat')} use:link>Start a new chat</a> instead.
            </p>
          </div>
        {:else if historyLoading && !items.length}
          <Skeleton rows={6} />
        {:else if !items.length}
          <div class="empty-state chat-welcome">
            <svg class="welcome-crow" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path
                d="m10 18l-1.65 4l-1.85-.75l1.45-3.525q-2.65-.7-4.3-2.85T2 10V6q0-1.65 1.175-2.825T6 2q.55 0 1.05.187t1 .388L14 5l-4 1.5V8l7.85 5H10q-.825 0-1.412-.587T8 11H6q0 1.65 1.175 2.825T10 15h11l1 5h-2l-1-2h-5v4h-2v-4zM5.288 5.288Q5 5.575 5 6t.288.713T6 7t.713-.288T7 6t-.288-.712T6 5t-.712.288"
              />
            </svg>
            <h3 class="welcome-title">
              {mate
                ? `Brief ${mate.name}`
                : currentAgent !== 'orchestrator'
                  ? `Chat with ${currentAgent}`
                  : 'What can bran do for you?'}
            </h3>
          </div>
        {:else}
          <ChatLog
            {items}
            {streamingIndex}
            {sources}
            {feedback}
            busy={streaming}
            strips={true}
            onaction={onComposerAction}
            onretry={retryFailed}
            onfeedback={activeChat ? rateAnswer : undefined}
            onregenerate={askAgain}
            onrunner={makeRunner}
            onopendoc={openDoc}
          />
          {#if followups.length && !streaming}
            <div class="followups">
              <div class="label-cap followups-label">Related</div>
              {#each followups as f (f)}
                <button class="followup" onclick={() => send(f)}>
                  <span>{f}</span>
                  <Icon name="arrow-right" size={14} />
                </button>
              {/each}
            </div>
          {/if}
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
          placeholder={mate ? `Message ${mate.name}…` : 'Message bran…'}
          onsubmit={() => send()}
          onstop={stop}
        >
          {#snippet leading()}
            {#if activeChat || mate}
              <span class="composer-agent agent-fixed"
                ><Icon name="bot" size={13} />{activeChat?.agent ?? pendingAgent ?? 'orchestrator'}</span
              >
            {:else}
              <div class="seg mode-seg" role="group" aria-label="How bran should handle this">
                <button
                  type="button"
                  class="seg-btn"
                  class:on={!pendingAgent}
                  title="bran answers, and hands work to its agents when that helps"
                  onclick={() => (pendingAgent = null)}>Ask</button
                >
                {#if hasResearch}
                  <button
                    type="button"
                    class="seg-btn"
                    class:on={pendingAgent === 'research'}
                    title="The research agent: searches the web and cites its sources"
                    onclick={() => (pendingAgent = 'research')}>Research</button
                  >
                {/if}
                {#if otherAgents.length}
                  <label
                    class="seg-btn agent-pick"
                    class:on={!!pendingAgent && pendingAgent !== 'research'}
                    title="Talk to one agent directly"
                  >
                    <span>{pendingAgent && pendingAgent !== 'research' ? pendingAgent : 'Agent'}</span>
                    <Icon name="chevron-down" size={12} />
                    <select
                      class="agent-select"
                      aria-label="Talk to one agent directly"
                      value={pendingAgent && pendingAgent !== 'research' ? pendingAgent : ''}
                      onchange={(e) => (pendingAgent = e.currentTarget.value || null)}
                    >
                      <option value="" disabled>Choose an agent</option>
                      {#each otherAgents as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
                    </select>
                  </label>
                {/if}
              </div>
            {/if}
          {/snippet}
        </Composer>
      </div>

      {#if isHome && mate}
        <p class="mate-intro">
          This is {mate.name}'s chat, and it stays open. Hand it work, ask what it has done, or tell it what
          to change: its scheduled runs read what you say here.
        </p>
      {:else if isHome}
        <div class="home-below">
          <label class="proj-pick" title="Start this chat inside a project">
            <Icon name="folder" size={13} />
            <span>{scopeId ? scopeName : 'Choose a project'}</span>
            <Icon name="chevron-down" size={12} />
            <select
              class="agent-select"
              aria-label="Project for this chat"
              value={scopeId ?? ''}
              onchange={(e) => switchScope(e.currentTarget.value || null)}
            >
              <option value="">No project</option>
              {#each workspace.projects as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
            </select>
          </label>
          <div class="starters">
            {#each suggestions as sug (sug.label)}
              <button class="starter" onclick={() => onComposerAction(sug.text, false)}>{sug.label}</button>
            {/each}
          </div>
          {#if recentOutputs.length}
            <div class="pickup">
              <div class="label-cap pickup-label">Pick up where your agents left off</div>
              {#each recentOutputs as r (r.id)}
                <div class="pickup-row">
                  <a href={href('/read/' + r.id)} use:link class="pickup-title">{outputTitle(r)}</a>
                  <span class="src src-{r.source}">{sourceLabel(r.source)}</span>
                  <span class="pickup-time">{relativeTime(r.started_at)}</span>
                  <button
                    class="btn-text"
                    onclick={() =>
                      onComposerAction(`Let's discuss the output of run ${r.id} (${outputTitle(r)}).`, false)}
                    >Discuss</button
                  >
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    </div>

    {#if doc}
      <!-- A document open beside the conversation takes the rail's place. -->
      <div class="doc-col">
        <DocPanel
          name={doc.name}
          title={doc.title}
          url={doc.url}
          rev={docRev}
          onclose={() => (doc = null)}
          onask={askAboutDoc}
        />
      </div>
    {:else if mate}
      <!-- A teammate's chat: who it is, its brief, its automations, its work. -->
      <aside class="chat-rail">
        <TeammateRail teammateId={mate.id} onchange={(t) => (mate = { id: t.id, name: t.name })} />
        {#if sources.length}
          <div class="rail-sources">
            <Section label="Sources">
              <SourcesPanel {sources} />
            </Section>
          </div>
        {/if}
      </aside>
    {:else if scopeId || sources.length}
      <!-- Right rail: project context (Cowork-style), only when in a project. -->
      <aside class="chat-rail">
        {#if scopeId}
          <div style="margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px;">
            <a href={href('/projects/' + encodeURIComponent(scopeId))} use:link class="rail-title"
              >{scopeName}</a
            >
            <span class="label-cap">Project</span>
          </div>
          <ProjectRail
            projectId={scopeId}
            mode="chat"
            sessionId={activeChat?.id ?? null}
            onopenfile={(name) => openDoc(name)}
          />
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
  /* Suggested next questions, under the newest answer. */
  .followups {
    display: flex;
    flex-direction: column;
    padding: 6px 2px 4px;
    animation: followups-in var(--dur-3) var(--transition) both;
  }
  .followups-label {
    margin-bottom: 4px;
  }
  .followup {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 9px 4px;
    background: transparent;
    border: 0;
    border-top: 1px solid var(--border);
    color: var(--fg);
    font: inherit;
    font-size: 14px;
    text-align: left;
    cursor: pointer;
    transition: color var(--dur-1) var(--transition);
  }
  .followup span {
    flex: 1;
    min-width: 0;
  }
  .followup :global(.icon) {
    color: var(--muted);
    transition:
      color var(--dur-1) var(--transition),
      transform var(--dur-1) var(--transition);
  }
  .followup:hover {
    color: var(--accent-soft);
  }
  .followup:hover :global(.icon) {
    color: var(--accent-soft);
    transform: translateX(2px);
  }
  @keyframes followups-in {
    from {
      opacity: 0;
      transform: translateY(4px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  /* The reading column centres itself on a wide pane; on a narrower one (a
     document or the rail beside it, a small window) it still needs a gutter. */
  .chat-main {
    padding: 0 14px;
  }

  /* A document beside the chat: about half the pane on a wide window, the
     whole window (over the chat) on a narrow one. */
  .doc-col {
    flex: 0 0 min(52%, 760px);
    min-width: 0;
    min-height: 0;
  }
  @media (max-width: 1100px) {
    .doc-col {
      position: fixed;
      inset: 0;
      z-index: 120;
      padding: calc(8px + var(--safe-top)) 8px calc(8px + var(--safe-bottom));
      background: var(--backdrop);
    }
  }

  .mate-intro {
    max-width: 560px;
    margin: 14px auto 0;
    font-size: 13px;
    line-height: 1.55;
    text-align: center;
    color: var(--muted);
  }
  .crumb-link {
    text-decoration: none;
  }

  .load-error {
    max-width: 720px;
    margin: 24px auto 0;
  }
  .load-error p {
    margin-top: 10px;
    font-size: 13px;
  }
  .rail-sources {
    margin-top: 10px;
  }
  /* The header's Sources button and its sheet exist for windows too narrow
     for the rail; on wide windows the rail is the one place for them. */
  .sources-btn {
    display: none;
  }
  .sources-sheet {
    display: none;
    margin: 12px 28px 0;
    max-height: 45vh;
    overflow-y: auto;
  }
  .sources-sheet-head {
    display: flex;
    align-items: center;
    margin-bottom: 8px;
  }
  @media (max-width: 1100px) {
    .sources-btn {
      display: inline-flex;
    }
    .sources-sheet {
      display: block;
    }
  }
  @media (max-width: 768px) {
    .sources-sheet {
      margin: 10px 16px 0;
    }
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
  /* Home screen: the question and the composer in the middle of the page. */
  .chat-main.home {
    justify-content: center;
    padding-bottom: 6vh;
  }
  .chat-main.home .chat-welcome {
    padding: 0 24px 6px;
  }
  .chat-main.home .welcome-title {
    font-size: 26px;
    letter-spacing: -0.01em;
    margin-bottom: 0;
  }
  .home-below {
    width: 100%;
    max-width: var(--chat-col);
    margin: 2px auto 0;
  }
  .proj-pick {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border-radius: var(--radius);
    font-size: 13px;
    color: var(--muted);
    cursor: pointer;
  }
  .proj-pick:hover,
  .proj-pick:focus-within {
    background: var(--surface2);
    color: var(--fg-bright);
  }
  .mode-seg {
    flex-wrap: nowrap;
  }
  .starters {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 16px;
  }
  .starter {
    padding: 7px 13px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
    color: var(--fg-dim);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    transition:
      background var(--dur-1) var(--transition),
      border-color var(--dur-1) var(--transition),
      color var(--dur-1) var(--transition);
  }
  .starter:hover {
    background: var(--surface2);
    border-color: var(--border2);
    color: var(--fg-bright);
  }
  .pickup {
    margin: 26px 0 0;
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
    width: 334px;
    flex-shrink: 0;
    overflow-y: auto;
    padding: 12px 14px 12px 4px; /* a gutter: the cards used to touch the window edge */
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
