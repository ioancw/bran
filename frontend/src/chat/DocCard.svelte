<script lang="ts" module>
  import type { ToolItem } from './events'

  /** The file name a save_document call wrote, from its result text
   * ("Saved “Title” → /path/to/name.html (12 KB)."). Null until it has. */
  export function savedDocName(tool: ToolItem): string | null {
    if (tool.status !== 'done' || tool.isError) return null
    const m = tool.resultText.match(/→\s*(.+?)\s*\(\s*[\d,.]+\s*KB\)/)
    if (!m) return null
    const name = m[1].replace(/\\/g, '/').split('/').pop() ?? ''
    return name.trim() || null
  }
</script>

<script lang="ts">
  // A document the agent saved (save_document), as a card in the conversation:
  // its title, and a way to open it beside the chat.
  import Icon from '../components/Icon.svelte'

  let { tool, onopen }: { tool: ToolItem; onopen: (name: string, title: string) => void } = $props()

  const title = $derived(String(tool.input?.title ?? '').trim() || 'Document')
  const name = $derived(savedDocName(tool))
</script>

{#if tool.status === 'running'}
  <div class="doc-card writing">
    <span class="doc-icon"><Icon name="file-text" size={18} /></span>
    <span class="doc-text">
      <span class="doc-title">{title}</span>
      <span class="doc-sub">Writing the document…</span>
    </span>
  </div>
{:else if name}
  <button class="doc-card" onclick={() => onopen(name, title)} title="Open beside the chat">
    <span class="doc-icon"><Icon name="file-text" size={18} /></span>
    <span class="doc-text">
      <span class="doc-title">{title}</span>
      <span class="doc-sub">{name}</span>
    </span>
    <span class="doc-open">Open<Icon name="arrow-right" size={13} /></span>
  </button>
{:else}
  <div class="doc-card failed" role="alert">
    <span class="doc-icon"><Icon name="alert-circle" size={18} /></span>
    <span class="doc-text">
      <span class="doc-title">{title}</span>
      <span class="doc-sub">{tool.resultText || 'The document could not be saved.'}</span>
    </span>
  </div>
{/if}

<style>
  .doc-card {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    max-width: 460px;
    padding: 10px 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    color: inherit;
    font: inherit;
    text-align: left;
    transition:
      background var(--dur-1) var(--transition),
      border-color var(--dur-1) var(--transition);
  }
  button.doc-card {
    cursor: pointer;
  }
  button.doc-card:hover {
    background: var(--surface2);
    border-color: var(--accent-soft);
  }
  .doc-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    height: 34px;
    flex-shrink: 0;
    border-radius: 8px;
    background: var(--accent-glow);
    color: var(--accent-soft);
  }
  .failed .doc-icon {
    background: var(--red-soft);
    color: var(--red);
  }
  .doc-text {
    display: flex;
    flex-direction: column;
    gap: 1px;
    flex: 1;
    min-width: 0;
  }
  .doc-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .doc-sub {
    font-size: 12px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .failed .doc-sub {
    white-space: normal;
  }
  .doc-open {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
    font-size: 13px;
    font-weight: 500;
    color: var(--accent-soft);
  }
</style>
