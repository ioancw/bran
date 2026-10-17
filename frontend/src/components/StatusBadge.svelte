<script lang="ts">
  // Run status: a coloured dot and the word. Colour is never the only signal.
  let { status }: { status: string } = $props()
  const tone: Record<string, string> = {
    completed: 'ok',
    failed: 'err',
    running: 'warn',
    pending: '',
    cancelled: '',
  }
  const t = $derived(tone[status] ?? '')
</script>

<span class="status" class:failed={status === 'failed'}>
  <span class="dot {t}" class:pulse={status === 'running'}></span>
  <span>{status}</span>
</span>

<style>
  .status {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    font-weight: 500;
    color: var(--fg-dim);
    white-space: nowrap;
  }
  .status.failed {
    color: var(--red);
  }
  .pulse {
    animation: brn-pulse 1.6s ease-in-out infinite;
  }
</style>
