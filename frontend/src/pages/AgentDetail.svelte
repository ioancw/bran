<script lang="ts">
  // Agent detail: the blueprint's config, plus the managed-agent actions —
  // run it now, schedule it, and see its runners + recent runs.
  import { api } from '../lib/api'
  import { href, link, navigate } from '../lib/router.svelte'
  import { fmtCost, relativeTime } from '../lib/time'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
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
    try {
      const [agents, schedules, rs] = await Promise.all([
        api.agents(), api.schedules(), api.runs({ agent: agentName, limit: 50 }),
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

  async function runNow() {
    if (firing) return
    firing = true
    try {
      const r = await api.newRun(agentName, task.trim() || `Run ${agentName}`)
      navigate('/runs/' + r.id)
    } catch (e) {
      error = String(e)
      firing = false
    }
  }
</script>

<Page title={agentName}>
  {#snippet subtitle()}agent{/snippet}
  {#snippet actions()}
    <button class="btn-outline" onclick={() => navigate('/runners?agent=' + encodeURIComponent(agentName))}>schedule →</button>
  {/snippet}

  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {:else if !agent}
    <div class="empty-state"><h3>unknown agent</h3></div>
  {:else}
    <div class="grid grid-cols-3 gap-6">
      <!-- Main: run now + recent runs -->
      <div class="col-span-2 space-y-6">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Run now</div>
          <textarea class="field" bind:value={task} rows="3"
                    placeholder={`Task for ${agentName}… (blank = a default run)`}
                    style="resize: none; font-family: var(--font-prose); font-size: 15px;"></textarea>
          <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
            <button class="btn-primary" disabled={firing} onclick={runNow}>{firing ? 'starting…' : 'run now →'}</button>
          </div>
        </div>

        <section>
          <div class="label-cap" style="margin-bottom: 8px;">Recent runs ({runs.length})</div>
          {#if runs.length}
            <div class="space-y-2">
              {#each runs.slice(0, 20) as r}
                <a href={href('/runs/' + r.id)} use:link class="row-link card-quiet" style="display: flex; gap: 10px; align-items: baseline; text-decoration: none;">
                  <span class="src">{r.source}</span>
                  <StatusBadge status={r.status} />
                  <span class="text-dim" style="font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.task}</span>
                  <span class="ml-auto text-muted" style="font-size: 11px; white-space: nowrap;">{relativeTime(r.started_at)} · {fmtCost(r.total_cost_usd)}</span>
                </a>
              {/each}
            </div>
          {:else}
            <div class="text-muted" style="font-size: 13px; font-style: italic;">no runs yet — fire one above.</div>
          {/if}
        </section>
      </div>

      <!-- Right: config + runners -->
      <aside class="col-span-1 space-y-4">
        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Config</div>
          <div class="text-dim" style="font-size: 13px; margin-bottom: 10px;">{agent.description}</div>
          <div style="font-size: 12px;" class="space-y-1">
            <div><span class="label-cap">model</span> <span class="mono text-dim">{agent.model ?? 'default'}</span></div>
            <div><span class="label-cap">tools</span> <span class="text-dim">{agent.tools.length}</span></div>
          </div>
          {#if agent.tools.length}
            <div style="display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px;">
              {#each agent.tools as t}<span class="src">{t}</span>{/each}
            </div>
          {/if}
          {#if agent.subagents.length}
            <div class="label-cap" style="margin-top: 10px;">delegates → {agent.subagents.join(', ')}</div>
          {/if}
        </div>

        <div class="card">
          <div class="label-cap" style="margin-bottom: 8px;">Runners ({runners.length})</div>
          {#if runners.length}
            <div class="space-y-2">
              {#each runners as s}
                <a href={href('/runners/' + encodeURIComponent(s.name))} use:link class="row-link" style="display: flex; gap: 8px; align-items: baseline; font-size: 12px; text-decoration: none;">
                  <span class="text-bright">{s.name}</span>
                  <span class="mono text-dim ml-auto">{s.cron}</span>
                </a>
              {/each}
            </div>
          {:else}
            <div class="text-muted" style="font-size: 12px; font-style: italic;">none — <button class="linkish" onclick={() => navigate('/runners?agent=' + encodeURIComponent(agentName))}>schedule one</button></div>
          {/if}
        </div>
      </aside>
    </div>
  {/if}
</Page>

<style>
  .row-link {
    padding: 7px 10px;
    border-radius: var(--radius);
    transition: background 0.12s var(--transition);
  }
  .row-link:hover { background: var(--surface2); }
  .src {
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    border-radius: var(--radius);
    background: var(--surface2);
    color: var(--muted);
  }
  .linkish {
    background: none; border: 0; padding: 0; cursor: pointer;
    color: var(--accent-soft); font: inherit; text-decoration: underline;
  }
</style>
