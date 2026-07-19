<script lang="ts">
  // Collapses a run of 3+ consecutive same-name tool calls into one expandable
  // block, so a fan-out (e.g. 12 WebSearches) stays scannable instead of a wall
  // of rows. Expands to the individual ToolBlocks. (Pattern from light_cc.)
  import { toolDisplayName, type ToolItem } from './events'
  import ToolBlock from './ToolBlock.svelte'

  let { name, tools }: { name: string; tools: ToolItem[] } = $props()
  let expanded = $state(false)

  const running = $derived(tools.some((t) => t.status === 'running'))
  const errored = $derived(tools.some((t) => t.isError))
  const dot = $derived(running ? 'running' : errored ? 'error' : 'done')
</script>

<div class="tool-group" class:expanded>
  <button type="button" class="tool-header" onclick={() => (expanded = !expanded)}>
    <span class="tool-status-dot {dot}"></span>
    <span class="tool-name" title={name}>{tools.length}× {toolDisplayName(name)}</span>
    <span class="tool-summary">{expanded ? 'hide' : 'show'} {tools.length} calls{errored ? ' · some errored' : ''}</span>
    <svg class="tool-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
      <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </button>
  {#if expanded}
    <div class="tool-group-body">
      {#each tools as t}<ToolBlock tool={t} />{/each}
    </div>
  {/if}
</div>

<style>
  .tool-group {
    border-left: 2px solid color-mix(in srgb, var(--muted) 45%, transparent);
    border-radius: var(--radius);
    margin: 1px 0;
  }
  .tool-group.expanded .tool-chevron { transform: rotate(180deg); }
  .tool-group-body {
    padding: 2px 0 4px 10px;
  }
</style>
