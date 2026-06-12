<script lang="ts">
  import { router } from './lib/router.svelte'
  import { installCodeCopyHandler } from './lib/markdown'
  import { api } from './lib/api'
  import { outputsSeen, isNewSince } from './lib/seen.svelte'
  import type { RunRecord } from './lib/types'
  import Sidebar from './components/Sidebar.svelte'
  import ConfirmHost from './components/ConfirmHost.svelte'
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
    projects: null, chat: null, runs: null, outputs: null, agents: null, runners: null,
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
    const fresh = fleetRuns.filter(
      (r) => r.status === 'completed' && (r.result ?? '').trim() && isNewSince(r.started_at, outputsSeen.at),
    ).length
    return { ...counts, outputs: fresh > 0 ? fresh : null }
  })

  const seg = $derived(router.route.segments)
</script>

<div class="min-h-screen flex">
  <Sidebar counts={navCounts} version="v0.1.0" />
  <main class="flex-1 min-w-0">
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
      <div class="px-8 py-6"><div class="empty-state"><h3>not found</h3></div></div>
    {/if}
  </main>
</div>
<ConfirmHost />
