<script lang="ts">
  import { toastState, dismiss } from '../lib/toast.svelte'
</script>

{#if toastState.items.length}
  <div class="toasts" role="status" aria-live="polite">
    {#each toastState.items as t (t.id)}
      <button class="toast toast-{t.tone}" onclick={() => dismiss(t.id)} title="dismiss">
        {t.text}
      </button>
    {/each}
  </div>
{/if}

<style>
  .toasts {
    position: fixed;
    bottom: 22px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 300;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    pointer-events: none;
  }
  .toast {
    pointer-events: auto;
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 999px;
    padding: 7px 16px;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--fg);
    cursor: pointer;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.3);
    animation: toast-in 0.18s var(--transition);
    max-width: min(480px, 90vw);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .toast-ok { border-color: color-mix(in srgb, var(--green) 45%, transparent); color: var(--green); }
  .toast-err { border-color: color-mix(in srgb, var(--red) 50%, transparent); color: var(--red); }
  @keyframes toast-in {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @media (prefers-reduced-motion: reduce) {
    .toast { animation: none; }
  }
</style>
