<script lang="ts">
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost, fmtDuration, localDateTime, shortId } from '../lib/time'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import type { RunRecord } from '../lib/types'

  let runs = $state<RunRecord[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)

  async function load() {
    try {
      runs = await api.runs({ limit: 200 })
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void load()
  })
</script>

<Page title="Runs">
  {#snippet subtitle()}{runs.length} recent{/snippet}
  {#if error}<div class="card" style="color: var(--red);">{errorText(error)}</div>{/if}
  {#if !loaded}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {:else if !runs.length}
    <div class="empty-state"><h3>no runs yet</h3></div>
  {:else}
    <div class="card flush" style="overflow-x: auto;">
      <table style="width: 100%; border-collapse: collapse; min-width: 640px;">
        <thead>
          <tr class="label-cap" style="text-align: left;">
            <th style="padding: 10px 14px;">ID</th><th>Agent</th><th>Status</th><th>Started</th><th>Dur</th><th>Cost</th><th>Task</th>
          </tr>
        </thead>
        <tbody>
          {#each runs as r}
            <tr style="border-top: 1px solid var(--border);">
              <td style="padding: 8px 14px;">
                <a href={href('/runs/' + r.id)} use:link class="mono" style="color: var(--fg-dim); text-decoration: none;">{shortId(r.id)}</a>
              </td>
              <td class="text-bright">{r.agent}</td>
              <td><StatusBadge status={r.status} /></td>
              <td class="text-dim" style="font-size: 12px;">{localDateTime(r.started_at)}</td>
              <td class="num text-dim">{fmtDuration(r.duration_ms)}</td>
              <td class="num text-dim">{fmtCost(r.total_cost_usd)}</td>
              <td class="text-dim" style="max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.task}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</Page>
