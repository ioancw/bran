<script lang="ts">
  // The one page-level error surface: red-tinted card, human-readable message,
  // and — because every page owns a load() it can call again — a retry button,
  // so a failed fetch is a speed bump instead of a dead end.
  import { errorText } from '../lib/errors'

  let { error, onretry }: { error: unknown; onretry?: () => void } = $props()
</script>

<div class="card err-banner" role="alert">
  <span class="err-msg">{errorText(error)}</span>
  {#if onretry}<button class="btn-outline" onclick={onretry}>↻ retry</button>{/if}
</div>

<style>
  .err-banner {
    display: flex;
    align-items: center;
    gap: 12px;
    color: var(--red);
    border-color: color-mix(in srgb, var(--red) 30%, transparent);
  }
  .err-msg {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .err-banner button {
    flex-shrink: 0;
  }
</style>
