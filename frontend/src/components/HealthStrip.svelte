<script lang="ts">
  // One row of chips answering "can bran work right now?": the Claude login,
  // the integrations, background-run slots in use, and spend. Renders nothing
  // until the snapshot arrives (and nothing at all if it fails).
  import type { HealthSnapshot } from '../lib/types'

  let { health }: { health: HealthSnapshot | null } = $props()

  const usd = (n: number) => '$' + n.toFixed(2)

  const loginTitle = $derived.by(() => {
    if (!health) return ''
    const l = health.login
    if (!l.ok) return l.detail || 'Claude login expired'
    if (l.method === 'api_key') return 'Using an Anthropic API key'
    if (l.method === 'claude.ai')
      return `Signed in to claude.ai${l.subscription ? ` (${l.subscription})` : ''}`
    return 'No Claude login found'
  })
</script>

{#if health}
  <div class="strip" aria-label="Status">
    <span class="chip" class:bad={!health.login.ok} title={loginTitle}>
      <span class="dot" class:ok={health.login.ok} class:err={!health.login.ok}></span>
      {health.login.ok ? 'Login' : 'Login expired'}
    </span>
    <span
      class="chip"
      class:off={!health.integrations.tavily}
      title={health.integrations.tavily ? 'Tavily search is configured' : 'TAVILY_API_KEY is not set'}
    >
      <span class="dot" class:ok={health.integrations.tavily}></span>
      Tavily
    </span>
    <span
      class="chip"
      class:off={!health.integrations.webhook}
      title={health.integrations.webhook
        ? 'Push notifications are configured'
        : 'BRAN_NOTIFY_WEBHOOK_URL is not set'}
    >
      <span class="dot" class:ok={health.integrations.webhook}></span>
      Push
    </span>
    <span class="chip" title="Background runs executing right now, out of the concurrency limit">
      <span class="dot" class:warn={health.slots.in_use > 0}></span>
      {health.slots.in_use} of {health.slots.max} slots in use
    </span>
    <span class="chip spend" title="Estimated spend (client-side SDK estimate, not billing)">
      Today {usd(health.spend.today.cost)} · 7 days {usd(health.spend.week.cost)} · 30 days {usd(
        health.spend.month.cost,
      )}
    </span>
  </div>
{/if}

<style>
  .strip {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 18px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 9px;
    border-radius: 999px;
    background: var(--surface2);
    color: var(--fg-dim);
    font-size: 12px;
    font-weight: 500;
    line-height: 1.6;
    white-space: nowrap;
  }
  .chip.off {
    color: var(--muted);
  }
  .chip.bad {
    background: var(--red-soft);
    color: var(--red);
  }
  .chip.spend {
    font-variant-numeric: tabular-nums;
  }
</style>
