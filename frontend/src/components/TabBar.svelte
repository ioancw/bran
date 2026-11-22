<script lang="ts">
  // Phone navigation: the three places you go most are one tap away, and the
  // rest sit behind More (the sidebar, as a drawer). Hidden above phone width.
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
        : seg[0] === 'chat'
          ? 'chat'
          : seg[0] === 'outputs' || seg[0] === 'read'
            ? 'outputs'
            : 'more',
  )
</script>

<nav class="tabbar" aria-label="Main">
  <a
    href={href('/')}
    use:link
    class="tab"
    class:on={at === 'today'}
    aria-current={at === 'today' ? 'page' : undefined}
  >
    <Icon name="calendar" size={21} /><span>Today</span>
  </a>
  <a
    href={href('/chat')}
    use:link
    class="tab"
    class:on={at === 'chat'}
    aria-current={at === 'chat' ? 'page' : undefined}
  >
    <Icon name="message-square" size={21} /><span>Chat</span>
  </a>
  <a
    href={href('/outputs')}
    use:link
    class="tab"
    class:on={at === 'outputs'}
    aria-current={at === 'outputs' ? 'page' : undefined}
  >
    <span class="tab-icon">
      <Icon name="inbox" size={21} />
      {#if unread}<span class="tab-badge" aria-label="{unread} new">{unread > 9 ? '9+' : unread}</span>{/if}
    </span>
    <span>Outputs</span>
  </a>
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
