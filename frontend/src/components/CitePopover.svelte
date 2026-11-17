<script lang="ts">
  // One hover card for every citation chip in the app. The chips are plain
  // HTML inside rendered markdown (see Prose.citeify), so this listens at the
  // document and reads what to show from the chip's data attributes.
  import SourceIcon from './SourceIcon.svelte'

  interface Shown {
    domain: string
    title: string
    url: string
    via: string
    n: string
    left: number
    top: number
    above: boolean
  }

  let shown = $state<Shown | null>(null)
  let hideTimer: ReturnType<typeof setTimeout> | undefined
  let showTimer: ReturnType<typeof setTimeout> | undefined
  const WIDTH = 300

  function chipOf(target: EventTarget | null): HTMLElement | null {
    return target instanceof Element ? target.closest<HTMLElement>('.cite[data-cite-domain]') : null
  }
  function open(chip: HTMLElement) {
    const r = chip.getBoundingClientRect()
    const above = r.bottom + 150 > window.innerHeight && r.top > 160
    shown = {
      domain: chip.dataset.citeDomain ?? '',
      title: chip.dataset.citeTitle ?? '',
      url: chip.dataset.citeUrl ?? '',
      via: chip.dataset.citeVia ?? '',
      n: chip.dataset.citeN ?? '',
      // Lined up with the chip's left edge, kept inside the window.
      left: Math.max(8, Math.min(r.left, window.innerWidth - WIDTH - 8)),
      top: above ? r.top - 8 : r.bottom + 8,
      above,
    }
  }
  function scheduleHide() {
    clearTimeout(showTimer)
    clearTimeout(hideTimer)
    hideTimer = setTimeout(() => (shown = null), 140)
  }
  function onOver(e: Event) {
    const chip = chipOf(e.target)
    if (!chip) return
    clearTimeout(hideTimer)
    clearTimeout(showTimer)
    // A short delay, so sweeping the pointer across a paragraph shows nothing.
    showTimer = setTimeout(() => open(chip), e.type === 'focusin' ? 0 : 120)
  }
  function onOut(e: Event) {
    if (chipOf(e.target)) scheduleHide()
  }
  function shortUrl(url: string): string {
    try {
      const u = new URL(url)
      const path = decodeURIComponent(u.pathname).replace(/\/+$/, '')
      const s = u.hostname.replace(/^www\./, '') + path
      return s.length > 64 ? s.slice(0, 63) + '…' : s
    } catch {
      return url
    }
  }

  $effect(() => {
    const hide = () => {
      clearTimeout(showTimer)
      shown = null
    }
    document.addEventListener('mouseover', onOver)
    document.addEventListener('mouseout', onOut)
    document.addEventListener('focusin', onOver)
    document.addEventListener('focusout', onOut)
    window.addEventListener('scroll', hide, true)
    return () => {
      document.removeEventListener('mouseover', onOver)
      document.removeEventListener('mouseout', onOut)
      document.removeEventListener('focusin', onOver)
      document.removeEventListener('focusout', onOut)
      window.removeEventListener('scroll', hide, true)
      clearTimeout(showTimer)
      clearTimeout(hideTimer)
    }
  })
</script>

{#if shown}
  <div
    class="cite-pop card elev-md"
    class:above={shown.above}
    role="tooltip"
    style="left: {shown.left}px; top: {shown.top}px; width: {WIDTH}px;"
    onmouseenter={() => clearTimeout(hideTimer)}
    onmouseleave={scheduleHide}
  >
    <div class="pop-head">
      <SourceIcon domain={shown.domain} size={16} />
      <span class="pop-domain">{shown.domain}</span>
      {#if shown.n}<span class="pop-n">Source {shown.n}</span>{/if}
    </div>
    {#if shown.title}<div class="pop-title">{shown.title}</div>{/if}
    {#if shown.url}
      <div class="pop-url">{shortUrl(shown.url)}</div>
    {:else}
      <div class="pop-url">Cited by name only; the answer gave no link.</div>
    {/if}
    {#if shown.via === 'fetched'}<div class="pop-via">Read by the agent</div>{/if}
  </div>
{/if}

<style>
  .cite-pop {
    position: fixed;
    z-index: 300;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 5px;
    pointer-events: auto;
    animation: pop-in var(--dur-1) var(--transition) both;
  }
  .cite-pop.above {
    transform: translateY(-100%);
    animation-name: pop-in-above;
  }
  .pop-head {
    display: flex;
    align-items: center;
    gap: 7px;
    min-width: 0;
  }
  .pop-domain {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 13px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .pop-n,
  .pop-via {
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
  }
  .pop-title {
    font-size: 13px;
    line-height: 1.4;
    color: var(--fg);
    overflow-wrap: anywhere;
  }
  .pop-url {
    font-size: 12px;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  @keyframes pop-in {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
  @keyframes pop-in-above {
    from {
      opacity: 0;
      transform: translateY(-100%);
    }
    to {
      opacity: 1;
      transform: translateY(-100%);
    }
  }
</style>
