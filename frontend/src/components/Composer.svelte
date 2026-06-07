<script lang="ts">
  // The shared "type here" input — a solid focus-ring surface, a footer row,
  // and a round accent send button, with built-in / command + @ agent
  // autocomplete (when a `catalog` is passed). Used by the chat, the project
  // launcher, and "Run now" so the input behaves identically everywhere.
  //
  // The parent owns the value (bind:value) and what happens on submit
  // (onsubmit) — e.g. send a message, start a chat, fire a run.
  import type { Snippet } from 'svelte'
  import type { Catalog } from '../lib/types'

  let {
    value = $bindable(''),
    placeholder = 'Message…',
    catalog,
    hint = '',
    busy = false,
    rows = 2,
    leading,
    onsubmit,
  }: {
    value?: string
    placeholder?: string
    catalog?: Catalog
    hint?: string
    busy?: boolean
    rows?: number
    leading?: Snippet
    onsubmit?: () => void
  } = $props()

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
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }
</script>

<div class="composer-wrap">
  {#if acOpen}
    <div class="ac-pop card">
      {#each acItems as it, i}
        <button type="button" class="ac-item" class:on={i === acIndex}
                onmousedown={(e) => { e.preventDefault(); pickAc(i) }}>
          <code class="ac-name">{it.trigger}{it.name}</code>
          <span class="ac-desc text-dim">{it.description}</span>
        </button>
      {/each}
    </div>
  {/if}
  <div class="composer">
    <textarea class="composer-input" bind:value oninput={refreshAc} onkeydown={onKeydown}
              {rows} {placeholder}></textarea>
    <div class="composer-footer">
      {#if leading}{@render leading()}{/if}
      {#if hint}<span class="composer-hint">{hint}</span>{/if}
      <button class="composer-send" disabled={busy} onclick={submit} aria-label="Send">
        {#if busy}
          <span class="composer-spin"></span>
        {:else}
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
        {/if}
      </button>
    </div>
  </div>
</div>

<style>
  .composer-wrap { position: relative; }
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
