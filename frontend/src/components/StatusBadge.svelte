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
  <span style="font-size: 9px;">{p[1]}</span>
  <span>{p[2]}</span>
</span>
