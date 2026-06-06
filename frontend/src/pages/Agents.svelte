<script lang="ts">
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
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
  const scheduleAgent = (name: string) => navigate('/runners?agent=' + encodeURIComponent(name))
</script>

<Page title="Agents">
  {#snippet subtitle()}the fleet · {agents.length}{/snippet}
  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {:else if !agents.length}
    <div class="empty-state"><h3>no agents</h3></div>
  {:else}
    <div class="card flush" style="overflow-x: auto;">
      <table style="width: 100%; border-collapse: collapse; min-width: 640px;">
        <thead>
          <tr class="label-cap" style="text-align: left;">
            <th style="padding: 10px 14px;">Name</th><th>Model</th><th>Description</th><th>Tools</th><th>Delegates</th><th></th>
          </tr>
        </thead>
        <tbody>
          {#each agents as a}
            <tr style="border-top: 1px solid var(--border);">
              <td style="padding: 10px 14px;" class="text-bright">{a.name}</td>
              <td class="mono text-dim">{a.model ?? 'default'}</td>
              <td class="text-dim" style="max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{a.description}</td>
              <td class="num text-dim">{a.tools.length}</td>
              <td class="text-dim">{a.subagents.length ? a.subagents.join(', ') : '—'}</td>
              <td><button class="btn-ghost" onclick={() => scheduleAgent(a.name)} title="run this agent on a schedule">schedule →</button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</Page>
