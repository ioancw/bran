<script lang="ts">
  // The one empty-state pattern: a small icon, a plain title, a sentence on
  // what will appear here, and the action that fills it.
  import type { Snippet } from 'svelte'
  import Icon from './Icon.svelte'

  let {
    title,
    hint = '',
    icon = 'inbox',
    children,
  }: {
    title: string
    hint?: string
    icon?: string
    children?: Snippet
  } = $props()
</script>

<div class="empty-state">
  <div class="empty-icon"><Icon name={icon} size={18} /></div>
  <h3>{title}</h3>
  {#if hint}<p class="cta">{hint}</p>{/if}
  {#if children}
    <div class="empty-actions">{@render children()}</div>
  {/if}
</div>

<style>
  .empty-actions {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 16px;
    font-size: 13px;
  }
</style>
