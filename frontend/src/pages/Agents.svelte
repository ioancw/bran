<script lang="ts">
  import { api } from '../lib/api'
  import { navigate, href, link } from '../lib/router.svelte'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import type { AgentInfo } from '../lib/types'
  let agents = $state<AgentInfo[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  $effect(() => {
    void api.agents().then((a) => (agents = a)).catch((e) => (error = String(e))).finally(() => (loaded = true))
  })
  // A Runner is an agent on a schedule — launch the create flow prefilled.
  function scheduleAgent(name: string, e: Event) {
    e.preventDefault()
    e.stopPropagation()
    navigate('/runners?agent=' + encodeURIComponent(name))
  }
</script>

<Page title="Agents">
  {#snippet subtitle()}the fleet · {agents.length}{/snippet}
  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {:else if !agents.length}
    <div class="empty-state"><h3>no agents</h3></div>
  {:else}
    <!-- Agents are a capability catalog, not tabular records — cards surface
         the description, tools and delegates better than a table row. -->
    <div class="agent-grid">
      {#each agents as a}
        <a href={href('/agents/' + encodeURIComponent(a.name))} use:link class="card agent-card">
          <div class="agent-head">
            <span class="text-bright" style="font-weight: 600; font-size: 15px;">{a.name}</span>
            <span class="label-cap">{a.model ?? 'default'}</span>
            <button class="btn-ghost ml-auto" onclick={(e) => scheduleAgent(a.name, e)} title="run this agent on a schedule">schedule →</button>
          </div>
          <p class="text-dim" style="font-size: 13px; line-height: 1.5; margin: 8px 0 10px;">{a.description}</p>
          {#if a.tools.length}
            <div class="agent-tools">
              {#each a.tools.slice(0, 8) as t}<span class="chip">{t}</span>{/each}
              {#if a.tools.length > 8}<span class="label-cap" style="align-self: center;">+{a.tools.length - 8}</span>{/if}
            </div>
          {/if}
          {#if a.subagents.length}
            <div class="label-cap" style="margin-top: 10px;">delegates → {a.subagents.join(', ')}</div>
          {/if}
        </a>
      {/each}
    </div>
  {/if}
</Page>

<style>
  .agent-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 16px;
  }
  .agent-card {
    display: block;
    text-decoration: none;
    transition: border-color 0.15s var(--transition), transform 0.12s var(--transition);
  }
  .agent-card:hover {
    transform: translateY(-1px);
    border-color: var(--accent-soft);
  }
  .agent-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .agent-tools {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
</style>
