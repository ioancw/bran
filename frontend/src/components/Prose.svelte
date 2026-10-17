<script lang="ts" module>
  // The markdown pipeline (markdown-it + KaTeX + Prism grammars) is the
  // heaviest code in the app, so it loads as its own chunk on first use —
  // one shared promise across every Prose instance.
  let loader: Promise<typeof import('../lib/markdown')> | null = null
  const loadMd = () => (loader ??= import('../lib/markdown'))
</script>

<script lang="ts">
  // Renders markdown + KaTeX. Reactive: re-renders as `text` grows during
  // streaming. The container class `.prose-md` matches global.css styling;
  // pass class="reading" for the serif reading column (outputs, run results).
  let {
    text,
    streaming = false,
    class: klass = '',
  }: { text: string; streaming?: boolean; class?: string } = $props()

  let renderFn = $state<((src: string) => string) | null>(null)
  void loadMd().then((m) => (renderFn = m.renderMarkdown))

  const html = $derived(renderFn ? renderFn(text) : null)
</script>

<div class="prose-md {klass}">
  {#if html != null}
    <!-- eslint-disable-next-line svelte/no-at-html-tags -- renderMarkdown escapes by construction AND runs DOMPurify over the final document -->
    {@html html}{#if streaming}<span class="stream-cursor"></span>{/if}
  {:else}
    <!-- Pipeline chunk still fetching (first render only): show the raw text
         rather than a blank — it swaps to rendered markdown in place. -->
    <div style="white-space: pre-wrap;">{text}</div>
  {/if}
</div>
