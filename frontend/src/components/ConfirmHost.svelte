<script lang="ts">
  // Renders the app's single confirm modal. Mount once (in App). Esc cancels,
  // Enter confirms; clicking the backdrop cancels.
  import { fade, scale } from 'svelte/transition'
  import { confirmState, settleConfirm } from '../lib/confirm.svelte'

  function onKeydown(e: KeyboardEvent) {
    if (!confirmState.open) return
    if (e.key === 'Escape') { e.preventDefault(); settleConfirm(false) }
    if (e.key === 'Enter') { e.preventDefault(); settleConfirm(true) }
  }
  // Close only when the backdrop itself (not the dialog) is clicked. Keyboard
  // dismissal (Esc/Enter) is handled globally above.
  function onBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) settleConfirm(false)
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if confirmState.open}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="confirm-backdrop" transition:fade={{ duration: 120 }} onclick={onBackdrop}>
    <div class="confirm-box card" transition:scale={{ duration: 140, start: 0.96 }}
         role="dialog" aria-modal="true" tabindex="-1">
      <p class="confirm-msg">{confirmState.message}</p>
      <div class="confirm-actions">
        <button class="btn-ghost" onclick={() => settleConfirm(false)}>cancel</button>
        <button class="btn-danger" onclick={() => settleConfirm(true)}>{confirmState.confirmLabel}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .confirm-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 200;
  }
  .confirm-box {
    width: min(420px, calc(100vw - 32px));
    padding: 20px;
  }
  .confirm-msg {
    font-size: 14px;
    color: var(--fg-bright);
    line-height: 1.5;
    margin: 0 0 16px;
  }
  .confirm-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .btn-danger {
    background: var(--red, #c0392b);
    color: #fff;
    border: 1px solid transparent;
    padding: 5px 14px;
    border-radius: var(--radius);
    font-family: var(--font-ui);
    cursor: pointer;
  }
  .btn-danger:hover { filter: brightness(1.08); }
</style>
