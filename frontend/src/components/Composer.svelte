<script lang="ts">
  // The shared "type here" input — a solid focus-ring surface, a footer row,
  // and a round accent send button, with built-in / command + @ agent
  // autocomplete (when a `catalog` is passed). Used by the chat, the project
  // launcher, and "Run now" so the input behaves identically everywhere.
  //
  // The parent owns the value (bind:value) and what happens on submit
  // (onsubmit) — e.g. send a message, start a chat, fire a run.
  import { fly } from 'svelte/transition'
  import type { Snippet } from 'svelte'
  import { api } from '../lib/api'
  import type { Attachment, Catalog } from '../lib/types'

  let {
    value = $bindable(''),
    placeholder = 'Message…',
    catalog,
    hint = '',
    busy = false,
    rows = 2,
    leading,
    onsubmit,
    onstop,
    attach = false,
    attachments = $bindable([]),
  }: {
    value?: string
    placeholder?: string
    catalog?: Catalog
    hint?: string
    busy?: boolean
    rows?: number
    leading?: Snippet
    onsubmit?: () => void
    // When provided, the send button becomes a stop button while busy —
    // surfaces that can cancel their stream (the chat) pass this.
    onstop?: () => void
    // Attachments (opt-in): paperclip / drag-drop / paste files. Uploaded
    // immediately; the parent reads `attachments` on submit and clears it.
    attach?: boolean
    attachments?: Attachment[]
  } = $props()

  // --- attachments ---
  let attInput = $state<HTMLInputElement | null>(null)
  let attBusy = $state(false)
  let attError = $state('')
  let dragOver = $state(false)

  async function uploadAtt(files: FileList | File[]) {
    const list = Array.from(files)
    if (!list.length || attBusy) return
    attBusy = true
    attError = ''
    try {
      const saved = await api.uploadAttachments(list)
      attachments = [...attachments, ...saved]
    } catch (e) {
      attError = e instanceof Error ? e.message : 'upload failed'
    } finally {
      attBusy = false
    }
  }
  function onAttPick(e: Event) {
    const input = e.currentTarget as HTMLInputElement
    if (input.files?.length) void uploadAtt(input.files)
    input.value = ''
  }
  function onDrop(e: DragEvent) {
    if (!attach) return
    e.preventDefault()
    dragOver = false
    if (e.dataTransfer?.files?.length) void uploadAtt(e.dataTransfer.files)
  }
  function onPaste(e: ClipboardEvent) {
    if (!attach) return
    const files = e.clipboardData?.files
    if (files?.length) {
      e.preventDefault() // a pasted screenshot shouldn't also paste as text
      void uploadAtt(files)
    }
  }
  function removeAtt(i: number) {
    // Drop the reference only; the uploaded file on disk is harmless.
    attachments = attachments.filter((_, k) => k !== i)
  }

  // --- autocomplete (/ commands, @ agents) ---
  interface AcItem { trigger: string; name: string; description: string; token: string }
  let acOpen = $state(false)
  let acItems = $state<AcItem[]>([])
  let acIndex = $state(0)
  let acTokenStart = -1 // where the / or @ token being completed begins

  function refreshAc() {
    if (!catalog) { acOpen = false; return }
    // Match the token at the cursor (start of line OR after whitespace), so
    // mentions complete mid-sentence — not only as the first character.
    const m = value.match(/(^|\s)([/@])(\S*)$/)
    if (!m) { acOpen = false; return }
    const trigger = m[2]
    const query = m[3].toLowerCase()
    acTokenStart = (m.index ?? 0) + m[1].length
    if (trigger === '/') {
      acItems = catalog.commands
        .filter((c) => c.name.toLowerCase().startsWith(query))
        .map((c) => ({ trigger: '/', name: c.name, description: c.description, token: `/${c.name} ` }))
    } else {
      acItems = catalog.agents
        .filter((a) => a.name.toLowerCase().includes(query))
        .map((a) => ({ trigger: '@', name: a.name, description: a.description, token: `@${a.name} ` }))
    }
    acIndex = 0
    acOpen = acItems.length > 0
  }
  function pickAc(i: number) {
    const it = acItems[i]
    if (!it) return
    value = value.slice(0, acTokenStart < 0 ? 0 : acTokenStart) + it.token
    acOpen = false
  }
  function submit() {
    acOpen = false
    if (busy) return
    onsubmit?.()
  }
  function onKeydown(e: KeyboardEvent) {
    if (acOpen) {
      if (e.key === 'Escape') { e.preventDefault(); acOpen = false; return }
      if (e.key === 'ArrowDown') { e.preventDefault(); acIndex = Math.min(acItems.length - 1, acIndex + 1); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); acIndex = Math.max(0, acIndex - 1); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickAc(acIndex); return }
    }
    // `isComposing` guards against submitting mid-IME: pressing Enter to
    // confirm a Japanese/Chinese conversion must not also send the message.
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault()
      submit()
    }
  }
</script>

<div class="composer-wrap">
  {#if acOpen}
    <div class="ac-pop card elev-md" transition:fly={{ y: 4, duration: 120 }}>
      {#each acItems as it, i}
        <button type="button" class="ac-item" class:on={i === acIndex}
                onmousedown={(e) => { e.preventDefault(); pickAc(i) }}>
          <code class="ac-name">{it.trigger}{it.name}</code>
          <span class="ac-desc text-dim">{it.description}</span>
        </button>
      {/each}
    </div>
  {/if}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="composer" class:drag-over={dragOver}
       ondragover={(e) => { if (attach) { e.preventDefault(); dragOver = true } }}
       ondragleave={() => (dragOver = false)}
       ondrop={onDrop}>
    {#if attachments.length || attError}
      <div class="att-row">
        {#each attachments as a, i (a.path)}
          <span class="att-chip mono" title={a.path}>
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
            {a.name} <span class="text-muted">{a.size_human}</span>
            <button class="att-x" onclick={() => removeAtt(i)} aria-label="remove {a.name}">
              <svg width="9" height="9" viewBox="0 0 10 10" fill="none" aria-hidden="true"><path d="M2 2l6 6M8 2l-6 6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
            </button>
          </span>
        {/each}
        {#if attError}<span style="color: var(--red); font-size: 11px;">{attError}</span>{/if}
      </div>
    {/if}
    <textarea class="composer-input" bind:value oninput={refreshAc} onkeydown={onKeydown}
              onpaste={onPaste} {rows} {placeholder}></textarea>
    <div class="composer-footer">
      {#if leading}{@render leading()}{/if}
      {#if attach}
        <button class="att-btn" disabled={attBusy} onclick={() => attInput?.click()}
                title="attach files (or drag/paste them)" aria-label="Attach files">
          {#if attBusy}…{:else}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          {/if}
        </button>
        <input bind:this={attInput} type="file" multiple style="display: none;" onchange={onAttPick} />
      {/if}
      {#if hint}<span class="composer-hint">{hint}</span>{/if}
      {#if busy && onstop}
        <!-- Streaming and cancellable: the send button becomes Stop. -->
        <button class="composer-send stop" onclick={onstop} aria-label="Stop generating" title="stop generating">
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect x="1.5" y="1.5" width="9" height="9" rx="1.5" fill="currentColor"/></svg>
        </button>
      {:else}
        <button class="composer-send" disabled={busy} onclick={submit} aria-label="Send">
          {#if busy}
            <span class="composer-spin"></span>
          {:else}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
          {/if}
        </button>
      {/if}
    </div>
  </div>
</div>

<style>
  .composer-wrap { position: relative; }
  .composer.drag-over { outline: 2px dashed var(--accent-soft); outline-offset: -2px; }
  .att-row {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    padding: 8px 12px 0;
  }
  .att-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: var(--fg-dim);
    border: 1px solid var(--border2);
    border-radius: 999px;
    padding: 2px 8px;
  }
  .att-x {
    display: inline-flex;
    align-items: center;
    background: transparent;
    border: 0;
    color: var(--muted);
    cursor: pointer;
    padding: 0 2px;
    line-height: 1;
  }
  .att-x:hover { color: var(--red); }
  .att-btn {
    display: inline-flex;
    align-items: center;
    background: transparent;
    border: 0;
    cursor: pointer;
    color: var(--muted);
    font-size: 14px;
    padding: 2px 4px;
    transition: color var(--dur-1) var(--transition);
  }
  .att-btn:hover { color: var(--fg); }
  .ac-pop {
    position: absolute;
    bottom: 100%;
    left: 0;
    right: 0;
    margin-bottom: 6px;
    padding: 4px;
    max-height: 280px;
    overflow-y: auto;
    z-index: 50;
  }
  .ac-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    text-align: left;
    padding: 8px 10px;
    border: 0;
    border-radius: var(--radius);
    cursor: pointer;
    background: transparent;
  }
  .ac-item.on { background: var(--accent-glow); }
  .ac-name {
    font-family: var(--font-mono);
    font-size: 13px;
    min-width: 110px;
    flex-shrink: 0;
    color: var(--fg-bright);
  }
  .ac-item.on .ac-name { color: var(--accent-soft); }
  .ac-desc {
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
