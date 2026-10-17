<script lang="ts">
  // Shared page shell: one compact sticky header (title, optional subtitle and
  // actions) over a padded body. Every page uses this so headers line up and
  // spacing is uniform.
  import type { Snippet } from 'svelte'

  let {
    title,
    subtitle,
    actions,
    head, // optional: replace the whole default header with a custom bar (e.g. a breadcrumb)
    children,
    fill = false, // chat-style full-height pages opt out of the default padding rhythm
  }: {
    title?: string
    subtitle?: Snippet
    actions?: Snippet
    head?: Snippet
    children: Snippet
    fill?: boolean
  } = $props()
</script>

{#if head}
  <header class="page-header head-custom">{@render head()}</header>
{:else}
  <header class="page-header">
    {#if title}<h1>{title}</h1>{/if}
    {#if subtitle}<span class="subheading">{@render subtitle()}</span>{/if}
    {#if actions}<div class="page-actions">{@render actions()}</div>{/if}
  </header>
{/if}
<div class:page-body={!fill} class:page-fill={fill}>
  {@render children()}
</div>

<style>
  /* Full-height pages (chat) let their own layout own the vertical space. */
  .page-fill {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
</style>
