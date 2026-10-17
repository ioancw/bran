<script lang="ts">
  import { api } from '../lib/api'
  import { navigate, href, link } from '../lib/router.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Icon from '../components/Icon.svelte'
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

<Page title="Agent library">
  {#snippet subtitle()}{agents.length} {agents.length === 1 ? 'agent' : 'agents'}{/snippet}
  {#if error}
    <ErrorBanner {error} onretry={load} />
  {:else if !loaded}
    <Skeleton rows={5} />
  {:else if !agents.length}
    <EmptyState
      title="No agents defined"
      hint="Agents are defined in src/bran/agents.py or as markdown files in .claude/agents/. They will appear here."
      icon="bot"
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
              <span class="agent-name">{a.name}</span>
              <span class="agent-model">{a.model ?? 'default model'}</span>
            </div>
            <p class="agent-desc">{a.description}</p>
            {#if a.tools.length}
              <div class="agent-tools">
                {#each a.tools.slice(0, 8) as t (t)}<span class="chip">{t}</span>{/each}
                {#if a.tools.length > 8}<span class="agent-more">+{a.tools.length - 8} more</span>{/if}
              </div>
            {/if}
            {#if a.subagents.length}
              <div class="agent-delegates">Delegates to {a.subagents.join(', ')}</div>
            {/if}
          </a>
          <button
            class="btn-outline btn-sm agent-sched"
            onclick={() => scheduleAgent(a.name)}
            title="Run this agent on a schedule"
            aria-label="Schedule {a.name}"><Icon name="calendar" size={13} />Schedule</button
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
    gap: 12px;
  }
  .agent-cell {
    position: relative;
  }
  .agent-card {
    display: block;
    height: 100%;
    text-decoration: none;
  }
  .agent-card:hover {
    text-decoration: none;
  }
  .agent-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    /* keep the name/model row clear of the overlaid schedule button */
    padding-right: 100px;
  }
  .agent-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .agent-model {
    font-size: 12px;
    color: var(--muted);
  }
  .agent-desc {
    font-size: 13px;
    line-height: 1.5;
    color: var(--fg-dim);
    margin: 8px 0 10px;
  }
  .agent-sched {
    position: absolute;
    top: 12px;
    right: 12px;
  }
  .agent-tools {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
  }
  .agent-more {
    font-size: 12px;
    color: var(--muted);
  }
  .agent-delegates {
    font-size: 12px;
    color: var(--muted);
    margin-top: 10px;
  }
</style>
