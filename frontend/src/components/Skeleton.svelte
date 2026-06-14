<script lang="ts">
  // Loading placeholder: shimmering text-shaped lines instead of a bare
  // "loading…" string, so pages feel responsive while their data fetches.
  let { rows = 4, height = 13 }: { rows?: number; height?: number } = $props()
  // Varied widths read as "text", uniform ones read as "boxes".
  const widths = [92, 100, 74, 86, 58, 95]
</script>

<div class="skel" role="status" aria-label="loading">
  {#each Array(rows) as _, i}
    <div class="skel-line" style="height: {height}px; width: {widths[i % widths.length]}%;"></div>
  {/each}
</div>

<style>
  .skel {
    padding: 18px 4px;
  }
  .skel-line {
    border-radius: 5px;
    background: linear-gradient(90deg, var(--surface2) 25%, var(--border2) 50%, var(--surface2) 75%);
    background-size: 200% 100%;
    animation: skel-shimmer 1.5s ease-in-out infinite;
    margin-bottom: 12px;
  }
  .skel-line:last-child { margin-bottom: 0; }
  @keyframes skel-shimmer {
    from { background-position: 200% 0; }
    to   { background-position: -200% 0; }
  }
  @media (prefers-reduced-motion: reduce) {
    .skel-line { animation: none; background: var(--surface2); }
  }
</style>
