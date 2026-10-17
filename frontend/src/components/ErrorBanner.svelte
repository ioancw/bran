<script lang="ts">
  // The one page-level error surface: red-tinted card, human-readable message,
  // and — because every page owns a load() it can call again — a retry button.
  import { errorText } from '../lib/errors'
  import Icon from './Icon.svelte'

  let { error, onretry }: { error: unknown; onretry?: () => void } = $props()
</script>

<div class="card err-banner" role="alert">
  <Icon name="alert-circle" size={16} />
  <span class="err-msg">{errorText(error)}</span>
  {#if onretry}<button class="btn-outline btn-sm" onclick={onretry}
      ><Icon name="refresh" size={13} />Retry</button
    >{/if}
</div>

<style>
  .err-banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    color: var(--red);
    background: var(--red-soft);
    border-color: color-mix(in srgb, var(--red) 30%, transparent);
    font-size: 13px;
  }
  .err-msg {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--fg);
  }
  .err-banner button {
    flex-shrink: 0;
  }
</style>
