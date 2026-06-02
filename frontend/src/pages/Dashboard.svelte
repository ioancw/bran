<script lang="ts">
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { fmtCost } from '../lib/time'
  import type { DashboardData } from '../lib/types'

  let data = $state<DashboardData | null>(null)
  let error = $state<string | null>(null)
  $effect(() => {
    void api.dashboard().then((d) => (data = d)).catch((e) => (error = String(e)))
  })
</script>

<header class="page-header">
  <h1>Dashboard</h1>
  <span class="subheading">{data?.today_label ?? ''}</span>
</header>
<div class="px-8 py-6 space-y-6">
  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}
  {#if data}
    <div class="grid grid-cols-4 gap-4">
      <div class="card"><div class="label-cap">completed today</div><div class="text-bright" style="font-size: 28px;">{data.stats.runs_completed}</div></div>
      <div class="card"><div class="label-cap">running</div><div class="text-bright" style="font-size: 28px;">{data.stats.runs_running}</div></div>
      <div class="card"><div class="label-cap">failed</div><div class="text-bright" style="font-size: 28px;">{data.stats.runs_failed}</div></div>
      <div class="card"><div class="label-cap">spend today</div><div class="text-bright" style="font-size: 28px;">{fmtCost(data.stats.total_cost_usd)}</div></div>
    </div>

    {#if data.upcoming.length}
      <div>
        <div class="label-cap" style="margin-bottom: 8px;">upcoming</div>
        <div class="space-y-2">
          {#each data.upcoming as u}
            <div class="card-quiet" style="display: flex; gap: 10px; align-items: baseline;">
              <span class="text-bright">{u.name}</span>
              <span class="mono text-accent-soft">{u.agent}</span>
              <span class="mono text-dim">{u.cron}</span>
              <span class="ml-auto label-cap">{u.countdown}</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    {#each data.buckets as bucket}
      <div>
        <div class="label-cap" style="margin-bottom: 8px;">{bucket.label}</div>
        <div class="grid grid-cols-2 gap-4">
          {#each bucket.items as it}
            <a href={href('/briefings/' + encodeURIComponent(it.href.split('/').pop() ?? ''))} use:link class="card" style="text-decoration: none; display: block;">
              <div style="display: flex; align-items: baseline; gap: 8px;">
                <span class="text-bright" style="font-weight: 600;">{it.title}</span>
                <span class="ml-auto label-cap">{it.time_label}</span>
              </div>
              <p class="text-dim" style="font-size: 13px; margin-top: 6px;">{it.snippet}</p>
            </a>
          {/each}
        </div>
      </div>
    {/each}
  {/if}
</div>
