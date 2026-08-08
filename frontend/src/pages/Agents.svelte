<script lang="ts">
  import { api } from '../lib/api'
  import { navigate, href, link } from '../lib/router.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import type { AgentInfo } from '../lib/types'
  let agents = $state<AgentInfo[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)
  async function load() {
    error = null
    try {
      agents = await api.agents()
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void load()
  })
  // A Runner is an agent on a schedule — launch the create flow prefilled.
  function scheduleAgent(name: string) {
    navigate('/runners?agent=' + encodeURIComponent(name))
  }
</script>

<Page title="Agents">
  {#snippet subtitle()}the fleet · {agents.length}{/snippet}
  {#if error}
    <ErrorBanner {error} onretry={load} />
  {:else if !loaded}
    <Skeleton rows={5} />
  {:else if !agents.length}
    <EmptyState
      title="no agents"
      hint="define one in src/bran/agents.py or drop a markdown file in .claude/agents/"
    />
  {:else}
    <!-- Agents are a capability catalog, not tabular records — cards surface
         the description, tools and delegates better than a table row. -->
    <div class="agent-grid">
      {#each agents as a (a.name)}
        <!-- The schedule button sits OUTSIDE the anchor (absolutely positioned
             over it) — interactive content inside <a> is invalid HTML and
             unreachable for keyboard/screen-reader users. -->
        <div class="agent-cell">
          <a href={href('/agents/' + encodeURIComponent(a.name))} use:link class="card agent-card">
            <div class="agent-head">
              <span class="text-bright" style="font-weight: 600; font-size: 15px;">{a.name}</span>
              <span class="label-cap">{a.model ?? 'default'}</span>
            </div>
            <p class="text-dim" style="font-size: 13px; line-height: 1.5; margin: 8px 0 10px;">
              {a.description}
            </p>
            {#if a.tools.length}
              <div class="agent-tools">
                {#each a.tools.slice(0, 8) as t (t)}<span class="chip">{t}</span>{/each}
                {#if a.tools.length > 8}<span class="label-cap" style="align-self: center;"
                    >+{a.tools.length - 8}</span
                  >{/if}
              </div>
            {/if}
            {#if a.subagents.length}
              <div class="label-cap" style="margin-top: 10px;">delegates → {a.subagents.join(', ')}</div>
            {/if}
          </a>
          <button
            class="btn-ghost agent-sched"
            onclick={() => scheduleAgent(a.name)}
            title="run this agent on a schedule"
            aria-label="schedule {a.name}">schedule →</button
          >
        </div>
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
  .agent-cell {
    position: relative;
  }
  .agent-card {
    display: block;
    height: 100%;
    text-decoration: none;
    transition:
      border-color 0.15s var(--transition),
      transform 0.12s var(--transition);
  }
  .agent-card:hover {
    transform: translateY(-1px);
    border-color: var(--accent-soft);
  }
  .agent-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    /* keep the name/model row clear of the overlaid schedule button */
    padding-right: 92px;
  }
  .agent-sched {
    position: absolute;
    top: 12px;
    right: 12px;
  }
  .agent-tools {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
</style>
