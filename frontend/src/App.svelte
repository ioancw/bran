<script lang="ts">
  import { router } from './lib/router.svelte'
  import { installCodeCopyHandler } from './lib/markdown'
  import { api } from './lib/api'
  import Sidebar from './components/Sidebar.svelte'
  import ConfirmHost from './components/ConfirmHost.svelte'
  import Chat from './pages/Chat.svelte'
  import Runs from './pages/Runs.svelte'
  import RunDetail from './pages/RunDetail.svelte'
  import Agents from './pages/Agents.svelte'
  import Runners from './pages/Runners.svelte'
  import Projects from './pages/Projects.svelte'
  import ProjectDetail from './pages/ProjectDetail.svelte'

  installCodeCopyHandler()

  // Nav counts (best-effort; nav still renders if these fail).
  let counts = $state<Record<string, number | null>>({
    projects: null, chat: null, runs: null, agents: null, runners: null,
  })
  $effect(() => {
    void (async () => {
      try {
        const [agents, projects, runners, runs] = await Promise.all([
          api.agents(), api.projects(), api.schedules(), api.runs({ limit: 200 }),
        ])
        counts.agents = agents.length
        counts.projects = projects.length
        counts.runners = runners.length
        counts.runs = runs.length
      } catch {
        /* leave counts null */
      }
    })()
  })

  const seg = $derived(router.route.segments)
</script>

<div class="min-h-screen flex">
  <Sidebar {counts} version="v0.1.0" />
  <main class="flex-1 min-w-0">
    {#if seg[0] === undefined}
      <Projects />
    {:else if seg[0] === 'chat'}
      <Chat sessionId={seg[1] ?? null} />
    {:else if seg[0] === 'runs' && seg[1]}
      <RunDetail runId={seg[1]} />
    {:else if seg[0] === 'runs'}
      <Runs />
    {:else if seg[0] === 'agents'}
      <Agents />
    {:else if seg[0] === 'runners'}
      <Runners />
    {:else if seg[0] === 'projects' && seg[1]}
      <ProjectDetail projectId={seg[1]} />
    {:else if seg[0] === 'projects'}
      <Projects />
    {:else}
      <div class="px-8 py-6"><div class="empty-state"><h3>not found</h3></div></div>
    {/if}
  </main>
</div>
<ConfirmHost />
