<script lang="ts">
  import { router } from './lib/router.svelte'
  import { installCodeCopyHandler } from './lib/markdown'
  import { api } from './lib/api'
  import { authState, clearAuthError } from './lib/auth.svelte'
  import { workspace } from './lib/workspace.svelte'
  import type { RunRecord } from './lib/types'
  import Sidebar from './components/Sidebar.svelte'
  import ConfirmHost from './components/ConfirmHost.svelte'
  import CommandPalette from './components/CommandPalette.svelte'
  import EmptyState from './components/EmptyState.svelte'
  import Toasts from './components/Toasts.svelte'
  import Today from './pages/Today.svelte'
  import Chat from './pages/Chat.svelte'
  import Runs from './pages/Runs.svelte'
  import Outputs from './pages/Outputs.svelte'
  import RunDetail from './pages/RunDetail.svelte'
  import Agents from './pages/Agents.svelte'
  import AgentDetail from './pages/AgentDetail.svelte'
  import Runners from './pages/Runners.svelte'
  import RunnerDetail from './pages/RunnerDetail.svelte'
  import Projects from './pages/Projects.svelte'
  import ProjectDetail from './pages/ProjectDetail.svelte'
  import Settings from './pages/Settings.svelte'

  installCodeCopyHandler()

  // Nav counts (best-effort; nav still renders if these fail).
  let counts = $state<Record<string, number | null>>({
    projects: null, runs: null, outputs: null, agents: null, runners: null,
  })
  let fleetRuns = $state<RunRecord[]>([])
  async function loadCounts() {
    try {
      const [agents, projects, runners, runs] = await Promise.all([
        api.agents(), api.projects(), api.schedules(),
        api.runs({ limit: 200, exclude_chats: true }),
      ])
      counts.agents = agents.length
      counts.projects = projects.length
      counts.runners = runners.length
      counts.runs = runs.length
      fleetRuns = runs
    } catch {
      /* leave counts null */
    }
  }
  $effect(() => {
    void workspace.activityTick // re-count when a turn finishes / Outputs marks read
    void loadCounts()
  })
  // Keep the badge honest when a runner fires while the tab is backgrounded.
  $effect(() => {
    const refresh = () => {
      if (document.visibilityState === 'visible') void loadCounts()
    }
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  })

  // The Outputs badge is an inbox count — *new deliveries since you last read
  // them* — not a total-ever. Hidden when there's nothing new; visiting
  // /outputs advances the seen marker and clears it reactively.
  const navCounts = $derived.by(() => {
    // Unread = a completed output you haven't opened yet (server read state).
    const fresh = fleetRuns.filter(
      (r) => r.status === 'completed' && (r.result ?? '').trim() && !r.read_at,
    ).length
    return { ...counts, outputs: fresh > 0 ? fresh : null }
  })

  const seg = $derived(router.route.segments)

  // Off-canvas sidebar on small screens; navigation closes the drawer.
  let sidebarOpen = $state(false)
  // Desktop: the rail can be hidden entirely (toggle button / Ctrl+B) for a
  // focused reading-writing surface. Persisted so the choice survives reloads.
  let sidebarCollapsed = $state(
    typeof localStorage !== 'undefined' && localStorage.getItem('bran.sidebarCollapsed') === '1',
  )
  function toggleSidebar() {
    sidebarCollapsed = !sidebarCollapsed
    try { localStorage.setItem('bran.sidebarCollapsed', sidebarCollapsed ? '1' : '0') } catch { /* ignore */ }
  }
  function onShellKeydown(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b') {
      e.preventDefault()
      toggleSidebar()
    }
  }
  // <main> is the scroll container now (the body is fixed-height), so reset its
  // scroll to the top on navigation — what window.scrollTo used to do for us.
  let mainEl = $state<HTMLElement | undefined>()
  $effect(() => {
    void router.route.path
    sidebarOpen = false
    mainEl?.scrollTo(0, 0)
  })

  function retryAuth() {
    clearAuthError()
    void loadCounts()
  }
</script>

<svelte:window onkeydown={onShellKeydown} />

<button class="nav-burger" aria-label="open navigation" onclick={() => (sidebarOpen = true)}>
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
    <path d="M4 7h16M4 12h16M4 17h16" />
  </svg>
</button>
{#if sidebarOpen}
  <button class="nav-overlay" aria-label="close navigation" onclick={() => (sidebarOpen = false)}></button>
{/if}

{#if authState.blocked}
  <div class="auth-banner" role="alert">
    <span>Not authorized — the server rejected a request{authState.message ? `: ${authState.message}` : '.'}</span>
    <span class="auth-hint">If you put bran behind a proxy or changed its token, refresh your session.</span>
    <button class="btn-outline" onclick={retryAuth}>retry</button>
    <button class="btn-primary" onclick={() => location.reload()}>reload app</button>
  </div>
{/if}

{#if sidebarCollapsed}
  <button class="sidebar-reopen" title="Show sidebar (Ctrl+B)" aria-label="show sidebar" onclick={toggleSidebar}>
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" />
    </svg>
  </button>
{/if}

<div class="app-shell flex" class:sidebar-collapsed={sidebarCollapsed}>
  <Sidebar counts={navCounts} version="v0.1.0" open={sidebarOpen} oncollapse={toggleSidebar} />
  <main class="flex-1 min-w-0 app-main" bind:this={mainEl}>
    {#if seg[0] === undefined}
      <Today />
    {:else if seg[0] === 'chat'}
      <Chat sessionId={seg[1] ?? null} />
    {:else if seg[0] === 'outputs'}
      <Outputs />
    {:else if seg[0] === 'runs' && seg[1]}
      <RunDetail runId={seg[1]} />
    {:else if seg[0] === 'runs'}
      <Runs />
    {:else if seg[0] === 'agents' && seg[1]}
      <AgentDetail agentName={decodeURIComponent(seg[1])} />
    {:else if seg[0] === 'agents'}
      <Agents />
    {:else if seg[0] === 'runners' && seg[1]}
      <RunnerDetail runnerName={decodeURIComponent(seg[1])} />
    {:else if seg[0] === 'runners'}
      <Runners />
    {:else if seg[0] === 'projects' && seg[1]}
      <ProjectDetail projectId={seg[1]} />
    {:else if seg[0] === 'projects'}
      <Projects />
    {:else if seg[0] === 'settings'}
      <Settings />
    {:else}
      <div class="px-8 py-6"><EmptyState title="not found" hint="that page doesn't exist — try Ctrl+K" /></div>
    {/if}
  </main>
</div>
<ConfirmHost />
<CommandPalette />
<Toasts />

<style>
  .auth-banner {
    position: sticky;
    top: 0;
    z-index: 250;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    padding: 10px 16px;
    background: color-mix(in srgb, var(--red) 12%, var(--surface));
    border-bottom: 1px solid color-mix(in srgb, var(--red) 40%, transparent);
    color: var(--fg-bright);
    font-size: 13px;
  }
  .auth-banner .auth-hint {
    color: var(--fg-dim);
    font-size: 12px;
  }
  .auth-banner button { margin-left: 0; }
</style>
