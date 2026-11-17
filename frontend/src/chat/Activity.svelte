<script lang="ts">
  // The agent's work between two pieces of prose, as ONE line. While it is
  // happening the line says what the agent is doing right now ("Reading
  // bbc.co.uk"); afterwards it says what the work amounted to ("3 web
  // searches · 4 pages read · 12s"). Opening it shows every step.
  import type { WorkItem } from './events'
  import { activityDuration, activitySummary, fmtSpan, stepLabel } from './activity'
  import Icon from '../components/Icon.svelte'
  import ToolBlock from './ToolBlock.svelte'

  let {
    steps,
    live = false,
  }: {
    steps: WorkItem[]
    // The turn is still going and this is its newest row: between steps the
    // agent is thinking, and the line should say so rather than look finished.
    live?: boolean
  } = $props()

  let expanded = $state(false)

  const runningStep = $derived.by(() => {
    for (let i = steps.length - 1; i >= 0; i--) {
      const s = steps[i]
      if (s.kind === 'tool' && s.status === 'running') return s
    }
    return null
  })
  const working = $derived(live || runningStep != null)
  const failed = $derived(steps.filter((s) => s.kind === 'tool' && s.isError).length)
  const label = $derived(
    working ? (runningStep ? stepLabel(runningStep) : 'Thinking') + '…' : activitySummary(steps),
  )
  const duration = $derived.by(() => {
    if (working) return ''
    const ms = activityDuration(steps)
    return ms != null && ms >= 500 ? fmtSpan(ms) : ''
  })
  // One page that would not load is a detail (the summary says "1 failed");
  // the row only turns red when nothing worked.
  const nTools = $derived(steps.filter((s) => s.kind === 'tool').length)
  const dot = $derived(working ? 'running' : failed && failed === nTools ? 'error' : 'done')
</script>

<div class="activity" class:expanded class:working>
  <button
    type="button"
    class="activity-head"
    aria-expanded={expanded}
    title={expanded ? 'Hide the steps' : 'Show the steps'}
    onclick={() => (expanded = !expanded)}
  >
    <span class="tool-status-dot {dot}"></span>
    <span class="activity-label" class:shimmer={working}>{label}</span>
    {#if !working && steps.length > 1}<span class="activity-count">{steps.length} steps</span>{/if}
    {#if duration}<span class="activity-time">{duration}</span>{/if}
    <Icon name="chevron-down" size={12} class="tool-chevron" />
  </button>
  {#if expanded}
    <div class="activity-body">
      {#each steps as s, i (s.kind === 'tool' ? (s.toolId ?? 'i' + i) : 'i' + i)}
        {#if s.kind === 'tool'}
          <ToolBlock tool={s} />
        {:else}
          <div class="thought">
            <div class="thought-head"><Icon name="brain" size={12} />Thinking</div>
            <div class="thought-body">{s.text}</div>
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .activity {
    border: 1px solid transparent;
    border-radius: 8px;
    margin: 1px 0;
  }
  .activity.expanded {
    background: var(--surface);
    border-color: var(--border);
    margin: 6px 0;
  }
  .activity-head {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-height: 30px;
    padding: 5px 10px;
    background: none;
    border: none;
    border-radius: 8px;
    color: inherit;
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .activity-head:hover {
    background: var(--surface2);
  }
  .activity.expanded .activity-head:hover {
    background: none;
  }
  .activity-label {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-dim);
  }
  /* A slow sweep of light across the live line: something is happening. */
  .activity-label.shimmer {
    background: linear-gradient(
      90deg,
      var(--muted) 0%,
      var(--muted) 35%,
      var(--fg-bright) 50%,
      var(--muted) 65%,
      var(--muted) 100%
    );
    background-size: 220% 100%;
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    animation: activity-sweep 1.8s linear infinite;
  }
  @keyframes activity-sweep {
    from {
      background-position: 100% 0;
    }
    to {
      background-position: -120% 0;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .activity-label.shimmer {
      animation: none;
      background: none;
      color: var(--fg-dim);
    }
  }
  .activity-count,
  .activity-time {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .activity-count {
    flex: 1;
  }
  .activity-time {
    margin-left: auto;
  }
  /* Only this row's own chevron — not those of the steps inside it. */
  .activity.expanded > .activity-head :global(.tool-chevron) {
    transform: rotate(180deg);
  }
  .activity-body {
    border-top: 1px solid var(--border);
    padding: 4px 8px 8px;
  }
  .thought {
    margin: 6px 2px;
    padding: 2px 0 2px 10px;
    border-left: 2px solid var(--border2);
  }
  .thought-head {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
  }
  .thought-body {
    margin-top: 3px;
    font-size: 13px;
    line-height: 1.55;
    color: var(--fg-dim);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
