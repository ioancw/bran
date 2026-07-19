<script lang="ts">
  // Renders the app's single confirm modal. Mount once (in App). Esc cancels;
  // clicking the backdrop cancels. Enter is NOT globally bound — these dialogs
  // guard destructive actions, so activation goes through the focused button
  // (initial focus lands on Cancel; the destructive path takes a deliberate
  // Tab/click). Focus is trapped inside while open and restored on close.
  import { fade, scale } from 'svelte/transition'
  import { confirmState, settleConfirm } from '../lib/confirm.svelte'

  let cancelBtn = $state<HTMLButtonElement | undefined>()
  let boxEl = $state<HTMLElement | undefined>()
  let restoreEl: HTMLElement | null = null

  $effect(() => {
    if (confirmState.open) {
      restoreEl = document.activeElement instanceof HTMLElement ? document.activeElement : null
      requestAnimationFrame(() => cancelBtn?.focus())
    } else if (restoreEl) {
      // Refocus the trigger if it survived the action (a deleted row's × won't).
      if (restoreEl.isConnected) restoreEl.focus()
      restoreEl = null
    }
  })

  function onKeydown(e: KeyboardEvent) {
    if (!confirmState.open) return
    if (e.key === 'Escape') {
      e.preventDefault()
      settleConfirm(false)
    } else if (e.key === 'Tab' && boxEl) {
      // Two-button trap: keep Tab cycling inside the dialog.
      const focusables = boxEl.querySelectorAll<HTMLElement>('button')
      if (!focusables.length) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }
  // Close only when the backdrop itself (not the dialog) is clicked.
  function onBackdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) settleConfirm(false)
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if confirmState.open}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="confirm-backdrop overlay-backdrop" role="presentation"
       transition:fade={{ duration: 120 }} onclick={onBackdrop}>
    <div class="confirm-box card elev-lg" bind:this={boxEl}
         transition:scale={{ duration: 140, start: 0.96 }}
         role="dialog" aria-modal="true" aria-labelledby="confirm-msg" tabindex="-1">
      <p class="confirm-msg" id="confirm-msg">{confirmState.message}</p>
      <div class="confirm-actions">
        <button class="btn-outline" bind:this={cancelBtn} onclick={() => settleConfirm(false)}>cancel</button>
        <button class="btn-danger" onclick={() => settleConfirm(true)}>{confirmState.confirmLabel}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .confirm-backdrop {
    display: flex;
    align-items: center;
    justify-content: center;
    /* Above the mobile sidebar drawer (230), nav-burger (240) and auth-banner
       (250) — a confirm fired from the drawer (e.g. delete chat) was rendering
       behind them. Stays below Toasts (300). */
    z-index: 270;
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
</style>
