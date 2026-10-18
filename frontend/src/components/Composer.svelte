<script lang="ts">
  // The shared "type here" input — a focus-ring surface, a footer row, and an
  // accent send button, with built-in / command + @ agent autocomplete (when a
  // `catalog` is passed). Used by the chat, the project launcher, and "Run now"
  // so the input behaves identically everywhere.
  //
  // The parent owns the value (bind:value) and what happens on submit
  // (onsubmit) — e.g. send a message, start a chat, fire a run.
  import { fly } from 'svelte/transition'
  import type { Snippet } from 'svelte'
  import { api } from '../lib/api'
  import Icon from './Icon.svelte'
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

  // --- textarea: auto-grow with content, focusable from the parent ---
  let taEl = $state<HTMLTextAreaElement | null>(null)
  // Stable id base for the autocomplete's listbox/option wiring.
  const uid = $props.id()

  /** Parents (chat page) call this to put the caret in the input. */
  export function focusInput() {
    taEl?.focus()
  }

  // Grow with the message up to ~8 lines, then scroll — a paragraph should
  // never be composed through a two-line letterbox. Runs on every value
  // change, including programmatic ones (starters, drafts, plan actions).
  const MAX_TA_HEIGHT = 220
  function autosize() {
    const el = taEl
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, MAX_TA_HEIGHT) + 'px'
  }
  $effect(() => {
    void value
    autosize()
  })

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
      attError = e instanceof Error ? e.message : 'Upload failed'
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
  interface AcItem {
    trigger: string
    name: string
    description: string
    token: string
  }
  let acOpen = $state(false)
  let acItems = $state<AcItem[]>([])
  let acIndex = $state(0)
  let acTokenStart = -1 // where the / or @ token being completed begins

  function refreshAc() {
    if (!catalog) {
      acOpen = false
      return
    }
    // Match the token at the cursor (start of line OR after whitespace), so
    // mentions complete mid-sentence — not only as the first character.
    const m = value.match(/(^|\s)([/@])(\S*)$/)
    if (!m) {
      acOpen = false
      return
    }
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
      if (e.key === 'Escape') {
        e.preventDefault()
        acOpen = false
        return
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        acIndex = Math.min(acItems.length - 1, acIndex + 1)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        acIndex = Math.max(0, acIndex - 1)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        pickAc(acIndex)
        return
      }
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
    <!-- Listbox semantics (as in CommandPalette): the textarea drives selection
         via aria-activedescendant, so AT hears what the arrow keys are doing. -->
    <div class="ac-pop card elev-md" role="listbox" id="{uid}-ac" transition:fly={{ y: 4, duration: 120 }}>
      {#each acItems as it, i (it.trigger + it.name)}
        <button
          type="button"
          class="ac-item"
          class:on={i === acIndex}
          role="option"
          id="{uid}-ac-{i}"
          aria-selected={i === acIndex}
          onmousedown={(e) => {
            e.preventDefault()
            pickAc(i)
          }}
        >
          <code class="ac-name">{it.trigger}{it.name}</code>
          <span class="ac-desc">{it.description}</span>
        </button>
      {/each}
    </div>
  {/if}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="composer"
    class:drag-over={dragOver}
    ondragover={(e) => {
      if (attach) {
        e.preventDefault()
        dragOver = true
      }
    }}
    ondragleave={() => (dragOver = false)}
    ondrop={onDrop}
  >
    {#if attachments.length || attError}
      <div class="att-row">
        {#each attachments as a, i (a.path)}
          <span class="chip att-chip" title={a.path}>
            <Icon name="file" size={11} />
            <span class="att-name">{a.name}</span>
            <span class="text-muted">{a.size_human}</span>
            <button class="btn-icon att-x" onclick={() => removeAtt(i)} aria-label="Remove {a.name}">
              <Icon name="x" size={11} />
            </button>
          </span>
        {/each}
        {#if attError}<span class="att-error">{attError}</span>{/if}
      </div>
    {/if}
    <textarea
      class="composer-input"
      bind:this={taEl}
      bind:value
      oninput={refreshAc}
      onkeydown={onKeydown}
      onpaste={onPaste}
      {rows}
      {placeholder}
      aria-label={placeholder}
      aria-controls={acOpen ? `${uid}-ac` : undefined}
      aria-activedescendant={acOpen ? `${uid}-ac-${acIndex}` : undefined}
      style="min-height: {rows * 1.5}em; max-height: {MAX_TA_HEIGHT}px; overflow-y: auto;"></textarea>
    <div class="composer-footer">
      {#if leading}{@render leading()}{/if}
      {#if attach}
        <button
          class="btn-icon"
          disabled={attBusy}
          onclick={() => attInput?.click()}
          title="Attach files (or drag or paste them)"
          aria-label="Attach files"
        >
          {#if attBusy}<Icon name="loader" size={15} class="spin" />{:else}<Icon
              name="paperclip"
              size={15}
            />{/if}
        </button>
        <input bind:this={attInput} type="file" multiple style="display: none;" onchange={onAttPick} />
      {/if}
      {#if hint}<span class="composer-hint">{hint}</span>{/if}
      {#if busy && onstop}
        <!-- Streaming and cancellable: the send button becomes Stop. -->
        <button
          class="composer-send stop"
          onclick={onstop}
          aria-label="Stop generating"
          title="Stop generating"
        >
          <Icon name="stop" size={14} stroke={2} />
        </button>
      {:else}
        <button class="composer-send" disabled={busy} onclick={submit} aria-label="Send" title="Send">
          {#if busy}
            <span class="composer-spin"></span>
          {:else}
            <Icon name="arrow-up" size={16} stroke={2.2} />
          {/if}
        </button>
      {/if}
    </div>
  </div>
</div>

<style>
  .composer-wrap {
    position: relative;
  }
  .composer.drag-over {
    outline: 2px dashed var(--accent);
    outline-offset: -2px;
  }
  .att-row {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    padding: 0 2px 8px;
  }
  .att-chip {
    gap: 6px;
    padding-right: 2px;
    font-family: var(--font-ui);
    font-size: 12px;
  }
  .att-name {
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .att-x {
    width: 20px;
    height: 20px;
  }
  .att-error {
    color: var(--red);
    font-size: 12px;
  }
  /* The hint is desktop-only: on a phone the keyboard's own send key wins. */
  @media (max-width: 768px) {
    .composer-hint {
      display: none;
    }
  }
  /* Narrow panes: the footer may wrap; the send button stays bottom-right. */
  :global(.composer-footer) {
    flex-wrap: wrap;
    row-gap: 6px;
  }
  :global(.icon.spin) {
    animation: composer-spin 0.9s linear infinite;
  }
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
    padding: 7px 10px;
    border: 0;
    border-radius: var(--radius);
    cursor: pointer;
    background: transparent;
    font-size: 13px;
  }
  .ac-item.on {
    background: var(--accent-glow);
  }
  .ac-name {
    font-family: var(--font-mono);
    font-size: 12.5px;
    min-width: 110px;
    flex-shrink: 0;
    color: var(--fg-bright);
  }
  .ac-item.on .ac-name {
    color: var(--accent-soft);
  }
  .ac-desc {
    color: var(--fg-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
