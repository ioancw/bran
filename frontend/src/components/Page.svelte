<script lang="ts">
  // Shared page shell: a consistent header (title + optional subtitle/actions)
  // and a content area that fades in via global.css's `.stagger-in`. Every page
  // uses this so headers and spacing are uniform and navigation feels smooth.
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
<div class="px-8 py-6 stagger-in" class:page-fill={fill}>
  {@render children()}
</div>

<style>
  /* A custom header bar (breadcrumb) drops the title/eyebrow grid for a simple
     baseline-aligned row. Higher specificity than the global .page-header grid. */
  .page-header.head-custom {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  /* Full-height pages (chat) let their own layout own the vertical space. Now
     that <main> is a flex column (.app-main) and the header is flex-shrink:0,
     this just fills the remaining height — no fragile calc(100vh - Npx). */
  .page-fill {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
</style>
