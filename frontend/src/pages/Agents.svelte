<script lang="ts">
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import type { AgentInfo } from '../lib/types'
  let agents = $state<AgentInfo[]>([])
  let error = $state<string | null>(null)
  $effect(() => {
    void api.agents().then((a) => (agents = a)).catch((e) => (error = String(e)))
  })
  // A Runner is an agent on a schedule — launch the create flow prefilled.
  const scheduleAgent = (name: string) => navigate('/runners?agent=' + encodeURIComponent(name))
</script>

<header class="page-header">
  <h1>Agents</h1>
  <span class="subheading">the fleet</span>
</header>
<div class="px-8 py-6">
  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}
  <div class="grid grid-cols-2 gap-4">
    {#each agents as a}
      <div class="card">
        <div style="display: flex; align-items: baseline; gap: 8px;">
          <span class="text-bright" style="font-weight: 600;">{a.name}</span>
          <span class="label-cap">{a.model ?? 'default'}</span>
          <button class="btn-ghost ml-auto" onclick={() => scheduleAgent(a.name)} title="run this agent on a schedule">schedule →</button>
        </div>
        <p class="text-dim" style="font-size: 13px; margin: 8px 0;">{a.description}</p>
        <div style="display: flex; flex-wrap: wrap; gap: 4px;">
          {#each a.tools.slice(0, 8) as t}<span class="pill mute" style="font-size: 10px;">{t}</span>{/each}
          {#if a.tools.length > 8}<span class="label-cap">+{a.tools.length - 8}</span>{/if}
        </div>
        {#if a.subagents.length}
          <div class="label-cap" style="margin-top: 8px;">delegates → {a.subagents.join(', ')}</div>
        {/if}
      </div>
    {/each}
  </div>
</div>
