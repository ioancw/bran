<script lang="ts">
  // Collapsible right-rail card: a heading with an optional action and a
  // chevron that expands/collapses the body.
  import { slide } from 'svelte/transition'
  import type { Snippet } from 'svelte'
  import Icon from './Icon.svelte'

  let {
    label,
    open = true,
    action,
    children,
  }: {
    label: string
    open?: boolean
    action?: Snippet
    children: Snippet
  } = $props()

  // `open` is only an initial default; the toggle owns the state thereafter.
  // svelte-ignore state_referenced_locally
  let expanded = $state(open)
</script>

<div class="card section">
  <div class="section-head">
    <button
      type="button"
      class="section-toggle"
      aria-expanded={expanded}
      onclick={() => (expanded = !expanded)}
    >
      <span class="section-chevron" class:open={expanded}><Icon name="chevron-down" size={14} /></span>
      <span class="section-label">{label}</span>
    </button>
    {#if action}<div class="section-action">{@render action()}</div>{/if}
  </div>
  {#if expanded}
    <div class="section-body" transition:slide={{ duration: 180 }}>
      {@render children()}
    </div>
  {/if}
</div>

<style>
  .section-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .section-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    background: transparent;
    border: 0;
    padding: 0;
    cursor: pointer;
    color: inherit;
    flex: 1;
    text-align: left;
    min-width: 0;
  }
  .section-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg);
  }
  .section-action {
    margin-left: auto;
  }
  .section-chevron {
    display: inline-flex;
    color: var(--muted);
    transition: transform var(--dur-2) var(--transition);
  }
  .section-chevron:not(.open) {
    transform: rotate(-90deg);
  }
  .section-body {
    margin-top: 10px;
  }
</style>
