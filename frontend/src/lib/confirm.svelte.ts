// A tiny promise-based confirm dialog, so destructive actions get a styled
// modal instead of the browser's native confirm() (which breaks the UI's look).
// Usage: `if (!(await confirmDialog('Delete X?'))) return`. A single
// <ConfirmHost/> mounted in App renders the modal and settles the promise.

export const confirmState = $state<{
  open: boolean
  message: string
  confirmLabel: string
  resolve: ((v: boolean) => void) | null
}>({
  open: false,
  message: '',
  confirmLabel: 'delete',
  resolve: null,
})

export function confirmDialog(message: string, confirmLabel = 'delete'): Promise<boolean> {
  return new Promise((resolve) => {
    confirmState.message = message
    confirmState.confirmLabel = confirmLabel
    confirmState.resolve = resolve
    confirmState.open = true
  })
}

/** Resolve the pending dialog (called by ConfirmHost). */
export function settleConfirm(value: boolean): void {
  confirmState.open = false
  const r = confirmState.resolve
  confirmState.resolve = null
  r?.(value)
}
