<script lang="ts">
  // Collapsible right-rail card (Cowork-style): a header with a label, optional
  // action, and a chevron that smoothly expands/collapses the body.
  import { slide } from 'svelte/transition'
  import type { Snippet } from 'svelte'

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
    <button type="button" class="section-toggle" onclick={() => (expanded = !expanded)}>
      <svg class="section-chevron" class:open={expanded} width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
        <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span class="label-cap">{label}</span>
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
    gap: 8px;
    background: transparent;
    border: 0;
    padding: 0;
    cursor: pointer;
    color: inherit;
    flex: 1;
    text-align: left;
  }
  .section-action {
    margin-left: auto;
  }
  .section-chevron {
    color: var(--muted);
    transition: transform 0.18s var(--transition);
  }
  .section-chevron.open {
    transform: rotate(0deg);
  }
  .section-chevron:not(.open) {
    transform: rotate(-90deg);
  }
  .section-body {
    margin-top: 10px;
  }
</style>
