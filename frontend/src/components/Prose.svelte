<script lang="ts">
  // Renders markdown + KaTeX. Reactive: re-renders as `text` grows during
  // streaming. The container class `.prose-md` matches global.css styling.
  import { renderMarkdown } from '../lib/markdown'
  let { text, streaming = false }: { text: string; streaming?: boolean } = $props()
  const html = $derived(renderMarkdown(text))
</script>

<div class="prose-md">
  {@html html}{#if streaming}<span class="stream-cursor"></span>{/if}
</div>
