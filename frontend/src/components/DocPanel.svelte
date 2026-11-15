<script lang="ts" module>
  export type DocKind = 'page' | 'pdf' | 'markdown' | 'text' | 'image' | 'other'

  const TEXT_EXT = new Set([
    'txt',
    'csv',
    'tsv',
    'json',
    'log',
    'tex',
    'py',
    'js',
    'ts',
    'yaml',
    'yml',
    'toml',
    'xml',
    'ini',
    'sql',
    'sh',
  ])
  const IMAGE_EXT = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'])

  /** How a file can be shown beside the chat, by its extension. */
  export function docKind(name: string): DocKind {
    const ext = name.toLowerCase().split('.').pop() ?? ''
    if (ext === 'html' || ext === 'htm') return 'page'
    if (ext === 'pdf') return 'pdf'
    if (ext === 'md' || ext === 'markdown') return 'markdown'
    if (TEXT_EXT.has(ext)) return 'text'
    if (IMAGE_EXT.has(ext)) return 'image'
    return 'other'
  }
</script>

<script lang="ts">
  // A document, open beside the conversation: the report the agent just
  // saved, or a file from the project. The chat stays live next to it, so
  // "tighten section 2" is one message away; when the agent saves the file
  // again, `rev` changes and the view reloads.
  //
  // Files are untrusted (an agent or an upload wrote them): the server sends
  // them with a sandbox policy, and the frame is sandboxed here as well.
  import Icon from './Icon.svelte'
  import Prose from './Prose.svelte'

  let {
    name,
    title = '',
    url,
    rev = 0,
    onclose,
    onask,
  }: {
    name: string
    title?: string
    url: string
    rev?: number
    onclose: () => void
    // "Ask about this" — seeds the composer with a reference to the document.
    onask?: () => void
  } = $props()

  const kind = $derived(docKind(name))
  const src = $derived(url + (rev ? `?v=${rev}` : ''))
  const MAX_TEXT = 400_000

  let text = $state<string | null>(null)
  let textError = $state('')
  $effect(() => {
    if (kind !== 'markdown' && kind !== 'text') return
    const target = src
    let live = true
    text = null
    textError = ''
    void fetch(target)
      .then(async (r) => {
        if (!r.ok) throw new Error(`The file could not be loaded (${r.status}).`)
        const body = await r.text()
        if (live) text = body.length > MAX_TEXT ? body.slice(0, MAX_TEXT) + '\n\n[… truncated …]' : body
      })
      .catch((e: unknown) => {
        if (live) textError = e instanceof Error ? e.message : 'The file could not be loaded.'
      })
    return () => {
      live = false
    }
  })

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && !e.defaultPrevented) {
      e.preventDefault()
      onclose()
    }
  }
</script>

<svelte:window onkeydown={onKeydown} />

<section class="doc-panel card" aria-label="Document: {title || name}">
  <header class="doc-head">
    <Icon name="file-text" size={15} class="text-muted" />
    <div class="doc-names">
      <span class="doc-title">{title || name}</span>
      {#if title && title !== name}<span class="doc-file">{name}</span>{/if}
    </div>
    {#if onask}
      <button class="btn-ghost btn-sm" onclick={onask} title="Refer to this document in your next message">
        <Icon name="message-square" size={13} />Ask
      </button>
    {/if}
    <a
      class="btn-icon"
      href={url}
      target="_blank"
      rel="noopener"
      title="Open in a new tab"
      aria-label="Open in a new tab"
    >
      <Icon name="external-link" size={14} />
    </a>
    <a
      class="btn-icon"
      href={url + '?download=1'}
      download={name}
      title="Download"
      aria-label="Download {name}"
    >
      <Icon name="download" size={14} />
    </a>
    <button class="btn-icon" onclick={onclose} title="Close (Esc)" aria-label="Close the document">
      <Icon name="x" size={14} />
    </button>
  </header>

  <div class="doc-body">
    {#if kind === 'page'}
      {#key src}
        <iframe class="doc-frame" title={title || name} {src} sandbox="allow-scripts allow-modals"></iframe>
      {/key}
    {:else if kind === 'pdf'}
      {#key src}
        <iframe class="doc-frame" title={title || name} {src}></iframe>
      {/key}
    {:else if kind === 'image'}
      <div class="doc-pad center"><img {src} alt={name} /></div>
    {:else if kind === 'markdown' || kind === 'text'}
      <div class="doc-pad">
        {#if textError}
          <p class="text-muted">{textError}</p>
        {:else if text == null}
          <p class="text-muted">Loading…</p>
        {:else if kind === 'markdown'}
          <Prose {text} class="reading" />
        {:else}
          <pre class="doc-text">{text}</pre>
        {/if}
      </div>
    {:else}
      <div class="doc-pad">
        <p class="text-muted">
          This kind of file can't be shown here. Open it in a new tab or download it with the buttons above.
        </p>
      </div>
    {/if}
  </div>
</section>

<style>
  .doc-panel {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    height: 100%;
    padding: 0;
    overflow: hidden;
    animation: doc-in var(--dur-3) var(--transition) both;
  }
  @keyframes doc-in {
    from {
      opacity: 0;
      transform: translateX(8px);
    }
    to {
      opacity: 1;
      transform: translateX(0);
    }
  }
  .doc-head {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 8px 8px 12px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .doc-names {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
  }
  .doc-title {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .doc-file {
    font-size: 11.5px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .doc-body {
    flex: 1;
    min-height: 0;
    overflow: auto;
    background: var(--bg);
  }
  .doc-frame {
    display: block;
    width: 100%;
    height: 100%;
    border: 0;
    /* A saved document carries its own (light) page styling. */
    background: #fff;
  }
  .doc-pad {
    padding: 18px 20px 28px;
  }
  .doc-pad.center {
    display: flex;
    justify-content: center;
  }
  .doc-pad img {
    max-width: 100%;
    height: auto;
    border-radius: 8px;
  }
  .doc-text {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 12.5px;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: var(--fg);
  }
</style>
