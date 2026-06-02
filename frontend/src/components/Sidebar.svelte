<script lang="ts">
  import { router, href, link } from '../lib/router.svelte'
  import { THEMES, getTheme, setTheme } from '../lib/theme'

  let { counts, version }: { counts: Record<string, number | null>; version: string } = $props()

  let theme = $state(getTheme())
  function onTheme(e: Event) {
    theme = (e.target as HTMLSelectElement).value
    setTheme(theme)
  }

  // Two domains: Projects (where you work) + running agents (define / schedule /
  // observe). Dashboard and Briefings were dropped — Runs is the output surface.
  const items = $derived([
    { key: 'projects', label: 'Projects', to: '/' },
    { key: 'chat', label: 'Chat', to: '/chat' },
    { key: 'agents', label: 'Agents', to: '/agents' },
    { key: 'runners', label: 'Runners', to: '/runners' },
    { key: 'runs', label: 'Runs', to: '/runs' },
  ])
  // Root and /projects/* both belong to Projects.
  const seg0 = $derived(router.route.segments[0] ?? '')
  const active = $derived(seg0 === '' || seg0 === 'projects' ? 'projects' : seg0)
</script>

<aside class="sidebar shrink-0 flex flex-col">
  <div class="px-5 py-5" style="border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px;">
    <a href={href('/')} use:link class="wordmark with-crow" style="text-decoration: none;">
      <svg class="wordmark-crow" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="m10 18l-1.65 4l-1.85-.75l1.45-3.525q-2.65-.7-4.3-2.85T2 10V6q0-1.65 1.175-2.825T6 2q.55 0 1.05.187t1 .388L14 5l-4 1.5V8l7.85 5H10q-.825 0-1.412-.587T8 11H6q0 1.65 1.175 2.825T10 15h11l1 5h-2l-1-2h-5v4h-2v-4zM5.288 5.288Q5 5.575 5 6t.288.713T6 7t.713-.288T7 6t-.288-.712T6 5t-.712.288" />
      </svg>
      <span>bran</span>
    </a>
    <span class="ml-auto label-cap">{version}</span>
  </div>

  <nav class="p-3 space-y-0.5 text-sm">
    {#each items as item}
      <a href={href(item.to)} use:link class="nav-item" class:active={active === item.key}>
        <span>{item.label}</span>
        {#if counts[item.key] != null}
          <span class="count">{counts[item.key]}</span>
        {/if}
      </a>
    {/each}
  </nav>

  <div class="mt-auto p-3 space-y-2" style="border-top: 1px solid var(--border);">
    <div class="label-cap px-3">Theme</div>
    <select class="field" value={theme} onchange={onTheme} style="font-size: 12px;">
      {#each THEMES as t}
        <option value={t.name}>{t.label}</option>
      {/each}
    </select>
  </div>
</aside>
