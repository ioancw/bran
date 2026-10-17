<script lang="ts">
  // Collapses a burst of 3+ consecutive tool calls (any names) into one
  // expandable row, so a fan-out (12 searches, a few reads) stays scannable
  // instead of a wall of rows. Expands to the individual ToolBlocks.
  import { groupLabel, type ToolItem } from './events'
  import Icon from '../components/Icon.svelte'
  import ToolBlock from './ToolBlock.svelte'

  let { tools }: { tools: ToolItem[]; name?: string } = $props()
  let expanded = $state(false)

  const running = $derived(tools.some((t) => t.status === 'running'))
  const errored = $derived(tools.some((t) => t.isError))
  const dot = $derived(running ? 'running' : errored ? 'error' : 'done')
  const label = $derived(groupLabel(tools))
  const nErr = $derived(tools.filter((t) => t.isError).length)
</script>

<div class="tool-group" class:expanded>
  <button
    type="button"
    class="tool-group-header"
    aria-expanded={expanded}
    aria-label="{tools.length} tool calls"
    onclick={() => (expanded = !expanded)}
  >
    <span class="tool-status-dot {dot}"></span>
    <span class="tool-name">{label}</span>
    <span class="tool-group-count">
      {#if running}running{:else if nErr}{nErr} failed{/if}
    </span>
    <Icon name="chevron-down" size={12} class="tool-chevron" />
  </button>
  {#if expanded}
    <div class="tool-group-body">
      {#each tools as t, i (t.toolId ?? i)}<ToolBlock tool={t} />{/each}
    </div>
  {/if}
</div>

<style>
  .tool-group .tool-name {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tool-group-count {
    text-align: right;
  }
</style>
