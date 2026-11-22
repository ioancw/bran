<script lang="ts">
  // Phone navigation: the places you go most are one tap away (the day, a
  // new chat, your conversations, what the agents produced), and the rest sit
  // behind More (the sidebar, as a drawer). Hidden above phone width.
  import { router, href, link } from '../lib/router.svelte'
  import Icon from './Icon.svelte'

  let {
    unread = null,
    moreOpen = false,
    onmore,
  }: {
    unread?: number | null
    moreOpen?: boolean
    onmore: () => void
  } = $props()

  const seg = $derived(router.route.segments)
  const at = $derived(
    moreOpen
      ? 'more'
      : seg[0] === undefined
        ? 'today'
        : seg[0] === 'chat' && !seg[1]
          ? 'new'
          : seg[0] === 'chat' || seg[0] === 'recents'
            ? 'chats'
            : seg[0] === 'outputs' || seg[0] === 'read'
              ? 'outputs'
              : 'more',
  )
  const tabs = [
    { key: 'today', label: 'Today', to: '/', icon: 'calendar' },
    { key: 'new', label: 'New chat', to: '/chat', icon: 'square-pen' },
    { key: 'chats', label: 'Chats', to: '/recents', icon: 'message-square' },
    { key: 'outputs', label: 'Outputs', to: '/outputs', icon: 'inbox' },
  ] as const
</script>

<nav class="tabbar" aria-label="Main">
  {#each tabs as t (t.key)}
    <a
      href={href(t.to)}
      use:link
      class="tab"
      class:on={at === t.key}
      aria-current={at === t.key ? 'page' : undefined}
    >
      <span class="tab-icon">
        <Icon name={t.icon} size={21} />
        {#if t.key === 'outputs' && unread}<span class="tab-badge" aria-label="{unread} new"
            >{unread > 9 ? '9+' : unread}</span
          >{/if}
      </span>
      <span>{t.label}</span>
    </a>
  {/each}
  <button class="tab" class:on={at === 'more'} aria-expanded={moreOpen} onclick={onmore}>
    <Icon name="menu" size={21} /><span>More</span>
  </button>
</nav>

<style>
  .tabbar {
    display: none;
  }
  @media (max-width: 768px) {
    .tabbar {
      display: flex;
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      /* above the drawer (230), so More closes what it opened */
      z-index: 245;
      height: calc(var(--tabbar-h) + var(--safe-bottom));
      padding-bottom: var(--safe-bottom);
      background: var(--surface);
      border-top: 1px solid var(--border);
    }
    /* The keyboard is up: the bar gives its room to what is being typed. */
    :global(:root.typing) .tabbar {
      display: none;
    }
    .tab {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 3px;
      padding: 0;
      background: none;
      border: 0;
      color: var(--muted);
      font: inherit;
      font-size: 11px;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
      -webkit-tap-highlight-color: transparent;
    }
    .tab.on {
      color: var(--accent);
    }
    .tab-icon {
      position: relative;
      display: inline-flex;
    }
    .tab-badge {
      position: absolute;
      top: -5px;
      left: 14px;
      min-width: 16px;
      height: 16px;
      padding: 0 4px;
      border-radius: 999px;
      background: var(--accent);
      color: var(--accent-fg);
      font-size: 10px;
      font-weight: 600;
      line-height: 16px;
      text-align: center;
    }
  }
</style>
