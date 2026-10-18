<script lang="ts">
  // Ctrl/Cmd+K command palette: jump to any page, project, runner, or recent
  // chat, plus inline pause/resume actions. Data comes from the workspace
  // store (projects, chats) and a lazy schedules fetch on open.
  import { fade, scale } from 'svelte/transition'
  import { api } from '../lib/api'
  import { navigate } from '../lib/router.svelte'
  import { workspace, loadProjects } from '../lib/workspace.svelte'
  import { toast } from '../lib/toast.svelte'
  import { errorText } from '../lib/errors'
  import { paletteSignal } from '../lib/palette.svelte'
  import type { RunRecord, ScheduleRecord } from '../lib/types'
  import { sourceLabel } from '../lib/labels'
  import { relativeTime } from '../lib/time'

  interface Cmd {
    label: string
    meta: string
    run: () => void | Promise<void>
    keep?: boolean // stay open after running (e.g. pause/resume toggles)
    section?: string // group header shown when it changes down the list
    detail?: string // second line: where the match was found
    key?: string
  }

  let open = $state(false)
  let q = $state('')
  let idx = $state(0)
  let inputEl = $state<HTMLInputElement | undefined>()
  let runners = $state<ScheduleRecord[]>([])
  let restoreEl: HTMLElement | null = null

  const NAV: [string, string][] = [
    ['Today', '/'],
    ['Projects', '/projects'],
    ['Runners', '/runners'],
    ['Outputs', '/outputs'],
    ['Runs', '/runs'],
    ['Agent library', '/agents'],
    ['Settings', '/settings'],
  ]

  const all = $derived.by((): Cmd[] => {
    const cmds: Cmd[] = [
      { label: 'New chat', meta: 'action', run: () => navigate('/chat') },
      ...NAV.map(([label, to]): Cmd => ({ label, meta: 'go to', run: () => navigate(to) })),
    ]
    for (const p of workspace.projects) {
      cmds.push({
        label: p.name,
        meta: 'project',
        run: () => navigate('/projects/' + encodeURIComponent(p.id)),
      })
      cmds.push({
        label: `New chat in ${p.name}`,
        meta: 'action',
        run: () => navigate('/chat?project=' + encodeURIComponent(p.id)),
      })
    }
    for (const r of runners) {
      cmds.push({
        label: r.name,
        meta: 'runner',
        run: () => navigate('/runners/' + encodeURIComponent(r.name)),
      })
      cmds.push({
        label: `${r.enabled ? 'Pause' : 'Resume'} ${r.name}`,
        meta: 'action',
        keep: true,
        run: async () => {
          try {
            const updated = await api.setScheduleEnabled(r.name, !r.enabled)
            runners = runners.map((x) => (x.name === updated.name ? updated : x))
            toast(`${updated.enabled ? 'resumed' : 'paused'} ${updated.name}`, 'ok')
          } catch (e) {
            toast(errorText(e), 'err')
          }
        },
      })
    }
    for (const c of workspace.chats) {
      cmds.push({
        label: c.title,
        meta: 'recent chat',
        run: () => navigate('/chat/' + encodeURIComponent(c.id)),
      })
    }
    return cmds
  })

  // Content search: from three characters, every conversation turn and output
  // whose prompt or result contains the text (server-side LIKE over runs).
  let content = $state<Cmd[]>([])
  let searchSeq = 0
  function snippet(text: string, needle: string, span = 70): string {
    const hay = text.replace(/\s+/g, ' ')
    const at = hay.toLowerCase().indexOf(needle.toLowerCase())
    if (at < 0) return hay.slice(0, span * 2) + (hay.length > span * 2 ? '…' : '')
    const start = Math.max(0, at - span)
    const end = Math.min(hay.length, at + needle.length + span)
    return (start > 0 ? '…' : '') + hay.slice(start, end) + (end < hay.length ? '…' : '')
  }
  function toCmd(r: RunRecord, needle: string): Cmd {
    const isChat = r.source === 'chat'
    const where = (r.result ?? '').toLowerCase().includes(needle.toLowerCase()) ? (r.result ?? '') : r.task
    const title = r.task.replace(/\s+/g, ' ').slice(0, 90)
    return {
      label: title.length < r.task.trim().length ? title + '…' : title,
      meta: isChat
        ? `conversation · ${relativeTime(r.started_at)}`
        : `${sourceLabel(r.source).toLowerCase()} · ${r.agent} · ${relativeTime(r.started_at)}`,
      section: isChat ? 'Found in conversations' : 'Found in outputs',
      detail: snippet(where, needle),
      key: 'run:' + r.id,
      run: () =>
        navigate(isChat && r.session_id ? '/chat/' + encodeURIComponent(r.session_id) : '/runs/' + r.id),
    }
  }
  $effect(() => {
    const needle = q.trim()
    const seq = ++searchSeq
    if (!open || needle.length < 3) {
      content = []
      return
    }
    const t = setTimeout(() => {
      void api
        .runs({ q: needle, limit: 30 })
        .then((rows) => {
          if (seq !== searchSeq) return
          const chats = rows.filter((r) => r.source === 'chat').slice(0, 8)
          const outputs = rows.filter((r) => r.source !== 'chat' && r.source !== 'subagent').slice(0, 8)
          content = [...chats, ...outputs].map((r) => toCmd(r, needle))
        })
        .catch(() => {
          if (seq === searchSeq) content = []
        })
    }, 180)
    return () => clearTimeout(t)
  })

  const shown = $derived.by(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return all.slice(0, 10)
    const jump = all
      .filter((c) => `${c.label} ${c.meta}`.toLowerCase().includes(needle))
      .slice(0, 8)
      .map((c) => ({ ...c, section: 'Jump to' }))
    return [...jump, ...content]
  })

  $effect(() => {
    void q
    idx = 0
  })

  // Open requests from elsewhere in the UI (the sidebar's search row).
  let handledRequests = paletteSignal.requests
  $effect(() => {
    if (paletteSignal.requests !== handledRequests) {
      handledRequests = paletteSignal.requests
      if (!open) openPalette()
    }
  })

  function openPalette() {
    restoreEl = document.activeElement instanceof HTMLElement ? document.activeElement : null
    open = true
    q = ''
    idx = 0
    void loadProjects()
    void api
      .schedules()
      .then((s) => (runners = s))
      .catch(() => {})
    requestAnimationFrame(() => inputEl?.focus())
  }
  function close() {
    open = false
    restoreEl?.focus()
    restoreEl = null
  }
  function pick(i: number) {
    const cmd = shown[i]
    if (!cmd) return
    if (!cmd.keep) close()
    void cmd.run()
  }

  function onWindowKeydown(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault()
      if (open) close()
      else openPalette()
    } else if (open && e.key === 'Escape') {
      e.preventDefault()
      close()
    }
  }
  function onInputKeydown(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      idx = Math.min(shown.length - 1, idx + 1)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      idx = Math.max(0, idx - 1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      pick(idx)
    } else if (e.key === 'Tab') {
      // Keep focus in the palette — arrows navigate, Tab has nowhere to go.
      e.preventDefault()
    }
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

{#if open}
  <button
    class="pal-overlay overlay-backdrop"
    aria-label="close palette"
    transition:fade={{ duration: 120 }}
    onclick={close}
  ></button>
  <div
    class="pal card elev-lg"
    role="dialog"
    aria-modal="true"
    aria-label="command palette"
    transition:scale={{ duration: 140, start: 0.98 }}
  >
    <input
      bind:this={inputEl}
      bind:value={q}
      class="pal-input"
      placeholder="Jump to, or do…"
      onkeydown={onInputKeydown}
      spellcheck="false"
      role="combobox"
      aria-expanded="true"
      aria-controls="pal-listbox"
      aria-activedescendant={shown.length ? `pal-opt-${idx}` : undefined}
      aria-autocomplete="list"
    />
    <div class="pal-list" role="listbox" id="pal-listbox" aria-label="commands">
      {#each shown as cmd, i (cmd.key ?? cmd.meta + cmd.label)}
        {#if cmd.section && cmd.section !== shown[i - 1]?.section}
          <div class="pal-section label-cap">{cmd.section}</div>
        {/if}
        <button
          class="pal-item"
          class:on={i === idx}
          class:two-line={!!cmd.detail}
          id="pal-opt-{i}"
          role="option"
          aria-selected={i === idx}
          tabindex="-1"
          onclick={() => pick(i)}
          onmouseenter={() => (idx = i)}
        >
          <span class="pal-label">{cmd.label}</span>
          <span class="pal-meta label-cap">{cmd.meta}</span>
          {#if cmd.detail}<span class="pal-detail">{cmd.detail}</span>{/if}
        </button>
      {:else}
        <div class="pal-none text-muted">
          {q.trim().length < 3
            ? `Nothing matches “${q}” — type 3+ characters to search conversations and outputs`
            : `Nothing matches “${q}”`}
        </div>
      {/each}
    </div>
    <div class="pal-foot"><kbd>↑</kbd><kbd>↓</kbd> navigate <kbd>↵</kbd> open <kbd>esc</kbd> close</div>
  </div>
{/if}

<style>
  .pal-overlay {
    z-index: 280;
    border: 0;
    padding: 0;
  }
  .pal {
    position: fixed;
    top: 16vh;
    left: 50%;
    /* Svelte's scale transition owns `transform` while animating, so centre
       with a margin instead of translateX(-50%). */
    width: min(560px, calc(100vw - 32px));
    margin-left: calc(-1 * min(560px, calc(100vw - 32px)) / 2);
    z-index: 290;
    padding: 8px;
  }
  .pal-input {
    width: 100%;
    background: transparent;
    border: 0;
    border-bottom: 1px solid var(--border);
    padding: 10px 10px 12px;
    font-family: var(--font-ui);
    font-size: 15px;
    color: var(--fg-bright);
    outline: none;
  }
  .pal-input::placeholder {
    color: var(--muted);
  }
  .pal-list {
    max-height: 320px;
    overflow-y: auto;
    padding: 6px 2px 2px;
  }
  .pal-item {
    display: flex;
    align-items: baseline;
    gap: 12px;
    width: 100%;
    text-align: left;
    background: transparent;
    border: 0;
    border-radius: var(--radius);
    padding: 8px 10px;
    cursor: pointer;
    color: var(--fg);
    font-size: 13px;
  }
  .pal-item.two-line {
    flex-wrap: wrap;
  }
  .pal-detail {
    flex-basis: 100%;
    font-size: 12px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 1px;
  }
  .pal-section {
    padding: 8px 10px 4px;
  }
  .pal-item.on {
    background: var(--accent-glow);
  }
  .pal-item.on .pal-label {
    color: var(--accent-soft);
  }
  .pal-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pal-meta {
    margin-left: auto;
    flex-shrink: 0;
  }
  .pal-none {
    padding: 14px 10px;
    font-size: 13px;
  }
  .pal-foot {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 10px 4px;
    border-top: 1px solid var(--border);
    margin-top: 4px;
    font-size: 11px;
    color: var(--muted);
  }
  .pal-foot kbd + kbd {
    margin-left: -4px;
  }
</style>
