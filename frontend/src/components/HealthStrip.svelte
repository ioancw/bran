<script lang="ts">
  // One row of chips answering "can bran work right now?": the Claude login,
  // the integrations, background-run slots in use, and spend. Renders nothing
  // until the snapshot arrives (and nothing at all if it fails).
  import type { HealthSnapshot } from '../lib/types'
  import Icon from './Icon.svelte'

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

  // What the last server start did about runner fires missed while it was down.
  const caughtUp = $derived(health?.scheduler?.caught_up ?? [])
  const skipped = $derived(health?.scheduler?.skipped ?? [])
  const names = (rows: { name: string }[]) => rows.map((r) => r.name).join(', ')

  // A phone shows one line (is anything wrong, and today's spend); tapping it
  // shows the chips. Anything wrong shows them without being asked.
  let open = $state(false)
  const trouble = $derived(!!health && (!health.login.ok || skipped.length > 0))
  const running = $derived(health?.slots.in_use ?? 0)
</script>

{#if health}
  <div class="strip" class:open={open || trouble} aria-label="Status">
    <button class="chip summary" aria-expanded={open || trouble} onclick={() => (open = !open)}>
      <span
        class="dot"
        class:ok={!trouble && !running}
        class:warn={!trouble && running > 0}
        class:err={trouble}
      ></span>
      {trouble ? 'Needs attention' : running ? `${running} running` : 'All OK'} · Today {usd(
        health.spend.today.cost,
      )}
      <Icon name="chevron-down" size={12} />
    </button>
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
    {#if caughtUp.length}
      <span
        class="chip"
        title={`The server was down when these were due, so they ran when it came back: ${names(caughtUp)}`}
      >
        <span class="dot warn"></span>
        Caught up {caughtUp.length} missed {caughtUp.length === 1 ? 'run' : 'runs'}
      </span>
    {/if}
    {#if skipped.length}
      <span
        class="chip bad"
        title={`Missed while the server was down, and too long ago to run now: ${names(skipped)}`}
      >
        <span class="dot err"></span>
        {skipped.length} missed {skipped.length === 1 ? 'run' : 'runs'} skipped
      </span>
    {/if}
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
  .summary {
    display: none;
    border: 0;
    font-family: inherit;
    cursor: pointer;
  }
  .strip.open .summary :global(.icon) {
    transform: rotate(180deg);
  }
  @media (max-width: 768px) {
    .summary {
      display: inline-flex;
      padding: 5px 11px;
      font-size: 13px;
    }
    .strip:not(.open) .chip:not(.summary) {
      display: none;
    }
    .strip {
      margin-bottom: 14px;
    }
  }
</style>
