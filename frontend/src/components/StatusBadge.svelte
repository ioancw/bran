<script lang="ts">
  // Mirror of partials/status_badge.html.
  let { status }: { status: string } = $props()
  const palette: Record<string, [string, string, string]> = {
    completed: ['ok', '✓', 'completed'],
    failed: ['err', '✗', 'failed'],
    running: ['warn', '◐', 'running'],
    pending: ['mute', '·', 'pending'],
    cancelled: ['mute', '⊘', 'cancelled'],
  }
  const p = $derived(palette[status] ?? ['mute', '?', status])
</script>

<span class="pill {p[0]}">
  <!-- A live run breathes; a frozen glyph reads as a hung process. -->
  <span style="font-size: 9px;" class:spin-glyph={status === 'running'}>{p[1]}</span>
  <span>{p[2]}</span>
</span>

<style>
  .spin-glyph {
    display: inline-block;
    animation: badge-pulse 1.6s ease-in-out infinite;
  }
  @keyframes badge-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }
</style>
