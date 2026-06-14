<script lang="ts">
  // The one empty-state pattern: bracket marks, serif italic title, mono CTA
  // hint, optional actions underneath. Keeps every page's "nothing here"
  // moment speaking with the same voice.
  import type { Snippet } from 'svelte'

  let {
    title,
    hint = '',
    children,
  }: {
    title: string
    hint?: string
    children?: Snippet
  } = $props()
</script>

<div class="empty-state">
  <div class="brackets">[&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;]</div>
  <h3>{title}</h3>
  {#if hint}<p class="cta">{hint}</p>{/if}
  {#if children}
    <div style="margin-top: 14px; font-size: 13px;">{@render children()}</div>
  {/if}
</div>
