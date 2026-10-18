<script lang="ts">
  // Agent detail: the blueprint's config, plus the managed-agent actions —
  // run it now, schedule it, and see its runners + recent runs.
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { fmtCost, relativeTime } from '../lib/time'
  import { errorText } from '../lib/errors'
  import { toast } from '../lib/toast.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Icon from '../components/Icon.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import Composer from '../components/Composer.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import { sourceLabel, sourceHint } from '../lib/labels'
  import type { AgentInfo, RunRecord, ScheduleRecord } from '../lib/types'

  let { agentName }: { agentName: string } = $props()

  let agent = $state<AgentInfo | null>(null)
  let runners = $state<ScheduleRecord[]>([])
  let runs = $state<RunRecord[]>([])
  let error = $state<string | null>(null)
  let loaded = $state(false)

  let task = $state('')
  let firing = $state(false)

  async function load() {
    error = null // clear a stale banner so a recovered load shows clean
    try {
      const [agents, schedules, rs] = await Promise.all([
        api.agents(),
        api.schedules(),
        api.runs({ agent: agentName, limit: 50 }),
      ])
      agent = agents.find((a) => a.name === agentName) ?? null
      runners = schedules.filter((s) => s.agent === agentName)
      runs = rs
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void agentName
    void load()
  })

  function schedule() {
    navigate('/runners?agent=' + encodeURIComponent(agentName))
  }

  async function runNow() {
    if (firing) return
    firing = true
    try {
      const r = await api.newRun(agentName, task.trim() || `Run ${agentName}`)
      navigate('/runs/' + r.id)
    } catch (e) {
      // An action failure is a toast, not the page-level banner — the banner's
      // retry reloads the page, which isn't what "run now failed" needs.
      toast(errorText(e), 'err')
      firing = false
    }
  }
</script>

<Page title={agentName}>
  {#snippet subtitle()}Agent{/snippet}
  {#snippet actions()}
    <button class="btn-outline" onclick={schedule}><Icon name="calendar" size={14} />Schedule</button>
  {/snippet}

  {#if error}
    <!-- A failed load is not "unknown agent" — don't show both, and offer retry. -->
    <ErrorBanner {error} onretry={load} />
  {:else if !loaded}
    <Skeleton rows={5} />
  {:else if !agent}
    <EmptyState title="Unknown agent" hint="Check the agent library for the current roster." icon="bot">
      <a href={href('/agents')} use:link class="btn-outline">Agent library</a>
    </EmptyState>
  {:else}
    <div class="grid grid-cols-3 gap-6">
      <!-- Main: run now + recent runs -->
      <div class="col-span-2 space-y-6">
        <div>
          <div class="label-cap" style="margin-bottom: 8px;">Run now</div>
          <Composer
            bind:value={task}
            hint="Starts a one-off run"
            busy={firing}
            rows={3}
            placeholder={`Task for ${agentName}… (blank = a default run)`}
            onsubmit={runNow}
          />
        </div>

        <section>
          <div class="label-cap" style="margin-bottom: 8px;">Recent runs · {runs.length}</div>
          {#if runs.length}
            <div class="run-list">
              {#each runs.slice(0, 20) as r (r.id)}
                <a href={href('/runs/' + r.id)} use:link class="run-row">
                  <span class="src src-{r.source}" title={sourceHint(r.source)}>{sourceLabel(r.source)}</span>
                  <StatusBadge status={r.status} />
                  <span class="run-task">{r.task}</span>
                  <span class="run-meta"
                    >{relativeTime(r.started_at)} · <span class="num">{fmtCost(r.total_cost_usd)}</span></span
                  >
                </a>
              {/each}
            </div>
          {:else}
            <p class="quiet">No runs yet. Start one above.</p>
          {/if}
        </section>
      </div>

      <!-- Right: config + runners -->
      <aside class="col-span-1 space-y-4">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Configuration</div>
          <p class="cfg-desc">{agent.description}</p>
          <dl class="cfg">
            <dt>Model</dt>
            <dd class="mono">{agent.model ?? 'default'}</dd>
            <dt>Tools</dt>
            <dd>{agent.tools.length}</dd>
          </dl>
          {#if agent.tools.length}
            <div class="cfg-tools">
              {#each agent.tools as t (t)}<span class="chip">{t}</span>{/each}
            </div>
          {/if}
          {#if agent.subagents.length}
            <div class="cfg-delegates">Delegates to {agent.subagents.join(', ')}</div>
          {/if}
        </div>

        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Runners · {runners.length}</div>
          {#if runners.length}
            <div class="runner-list">
              {#each runners as s (s.name)}
                <a href={href('/runners/' + encodeURIComponent(s.name))} use:link class="runner-row">
                  <span class="runner-name">{s.name}</span>
                  <span class="mono runner-cron">{s.run_at ? 'once' : s.cron}</span>
                </a>
              {/each}
            </div>
          {:else}
            <p class="quiet">
              None yet. <button class="btn-text" onclick={schedule}>Schedule one</button>
            </p>
          {/if}
        </div>
      </aside>
    </div>
  {/if}
</Page>

<style>
  .quiet {
    font-size: 13px;
    color: var(--muted);
  }
  .run-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .run-row {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 8px 10px;
    border-radius: var(--radius);
    text-decoration: none;
    transition: background var(--dur-1) var(--transition);
  }
  .run-row:hover {
    background: var(--surface2);
    text-decoration: none;
  }
  .run-task {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    color: var(--fg-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .run-meta {
    margin-left: auto;
    flex-shrink: 0;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }
  .cfg-desc {
    font-size: 13px;
    line-height: 1.5;
    color: var(--fg-dim);
    margin: 0 0 10px;
  }
  .cfg {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 4px 12px;
    margin: 0;
    font-size: 12.5px;
  }
  .cfg dt {
    color: var(--muted);
  }
  .cfg dd {
    margin: 0;
    color: var(--fg-dim);
  }
  .cfg-tools {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 10px;
  }
  .cfg-delegates {
    font-size: 12px;
    color: var(--muted);
    margin-top: 10px;
  }
  .runner-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .runner-row {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 6px 8px;
    margin: 0 -8px;
    border-radius: var(--radius);
    font-size: 13px;
    text-decoration: none;
    transition: background var(--dur-1) var(--transition);
  }
  .runner-row:hover {
    background: var(--surface2);
    text-decoration: none;
  }
  .runner-name {
    font-weight: 500;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .runner-cron {
    margin-left: auto;
    flex-shrink: 0;
    font-size: 12px;
    color: var(--fg-dim);
  }
</style>
