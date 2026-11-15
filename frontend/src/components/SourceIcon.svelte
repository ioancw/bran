<script lang="ts" module>
  // Domains whose icon request already failed this session — their tile is
  // drawn straight away instead of flashing a broken image first.
  // eslint-disable-next-line svelte/prefer-svelte-reactivity -- bookkeeping, read once per mount
  const missing = new Set<string>()
</script>

<script lang="ts">
  // A site's icon (served and cached by bran — GET /spa/favicon/<domain>),
  // falling back to a lettered tile in a colour that is stable per domain.
  import { domainHue } from '../chat/sources'

  let { domain, size = 16 }: { domain: string; size?: number } = $props()

  // Follows the domain; set by hand when this domain's image fails to load.
  let failed = $derived(missing.has(domain))
  function onError() {
    missing.add(domain)
    failed = true
  }
  const letter = $derived((domain.replace(/^www\./, '')[0] ?? '?').toUpperCase())
</script>

{#if failed || !domain}
  <span
    class="src-icon tile"
    style="width: {size}px; height: {size}px; font-size: {Math.round(size * 0.62)}px; --h: {domainHue(
      domain,
    )};"
    aria-hidden="true">{letter}</span
  >
{:else}
  <img
    class="src-icon"
    src={'/spa/favicon/' + encodeURIComponent(domain)}
    width={size}
    height={size}
    alt=""
    loading="lazy"
    decoding="async"
    onerror={onError}
  />
{/if}

<style>
  .src-icon {
    flex-shrink: 0;
    border-radius: 4px;
    object-fit: contain;
    vertical-align: -0.2em;
  }
  .tile {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-ui);
    font-weight: 600;
    line-height: 1;
    color: #fff;
    background: hsl(var(--h) 38% 44%);
    user-select: none;
  }
</style>
