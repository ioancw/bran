<script lang="ts">
  import { fly, fade } from 'svelte/transition'
  import { toastState, dismiss } from '../lib/toast.svelte'
</script>

{#if toastState.items.length}
  <div class="toasts">
    {#each toastState.items as t (t.id)}
      <!-- Errors announce assertively (role=alert); the rest stay polite. -->
      <div class="toast toast-{t.tone} elev-md" role={t.tone === 'err' ? 'alert' : 'status'}
           in:fly={{ y: 8, duration: 180 }} out:fade={{ duration: 140 }}>
        <span class="toast-icon" aria-hidden="true">
          {#if t.tone === 'ok'}
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.5l2.5 2.5 4.5-5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {:else if t.tone === 'err'}
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M6 3v3.6M6 8.8v.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
          {:else}
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="2" fill="currentColor"/></svg>
          {/if}
        </span>
        <span class="toast-text">{t.text}</span>
        <button class="toast-x" onclick={() => dismiss(t.id)} aria-label="dismiss">
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 2l6 6M8 2l-6 6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
      </div>
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
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: var(--surface);
    border: 1px solid var(--border2);
    border-radius: 999px;
    padding: 7px 10px 7px 14px;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--fg);
    max-width: min(480px, 90vw);
  }
  .toast-icon { display: inline-flex; flex-shrink: 0; }
  .toast-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .toast-x {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 18px;
    height: 18px;
    background: transparent;
    border: 0;
    border-radius: 50%;
    color: var(--muted);
    cursor: pointer;
    transition: color var(--dur-1) var(--transition), background var(--dur-1) var(--transition);
  }
  .toast-x:hover { color: var(--fg); background: var(--surface2); }
  .toast-ok { border-color: color-mix(in srgb, var(--green) 45%, transparent); color: var(--green); }
  .toast-err { border-color: color-mix(in srgb, var(--red) 50%, transparent); color: var(--red); }
</style>
