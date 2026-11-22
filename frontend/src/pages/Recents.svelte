<script lang="ts">
  // Every recent conversation as a roomy list, one tap from the phone's tab
  // bar. (The sidebar's own list is too tight to scroll with a thumb, and is
  // hidden at phone width.)
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { relativeTime } from '../lib/time'
  import { onReturn } from '../lib/refresh'
  import { loadProjects, projectName } from '../lib/workspace.svelte'
  import Page from '../components/Page.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import Icon from '../components/Icon.svelte'
  import type { ChatSummary } from '../lib/types'

  let chats = $state<ChatSummary[]>([])
  let loaded = $state(false)
  let error = $state<unknown>(null)
  let q = $state('')

  async function load() {
    try {
      chats = await api.chats()
      error = null
    } catch (e) {
      error = e
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void load()
    void loadProjects()
  })
  $effect(() => onReturn(() => void load()))

  const shown = $derived.by(() => {
    const want = q.trim().toLowerCase()
    return want ? chats.filter((c) => c.title.toLowerCase().includes(want)) : chats
  })
</script>

<Page title="Chats">
  {#snippet subtitle()}{chats.length} recent{/snippet}
  {#snippet actions()}
    <a href={href('/chat')} use:link class="btn-primary"
      ><Icon name="plus" size={14} stroke={2.2} />New chat</a
    >
  {/snippet}

  {#if error}
    <ErrorBanner {error} onretry={load} />
  {:else if !loaded}
    <Skeleton rows={6} />
  {:else if !chats.length}
    <EmptyState title="No conversations yet" hint="Start one with New chat." icon="message-square" />
  {:else}
    {#if chats.length > 8}
      <input
        class="field find"
        type="search"
        placeholder="Find a chat"
        aria-label="Find a chat"
        bind:value={q}
      />
    {/if}
    {#if shown.length}
      <div class="card chat-list">
        {#each shown as c (c.id)}
          <a href={href('/chat/' + c.id)} use:link class="chat-row">
            <span class="chat-title">{c.title}</span>
            <span class="chat-meta">
              {relativeTime(c.updated_at)}{#if c.project_id && projectName(c.project_id)}
                <span class="chat-proj">{projectName(c.project_id)}</span>{/if}
            </span>
          </a>
        {/each}
      </div>
    {:else}
      <p class="none">No chat matches "{q.trim()}".</p>
    {/if}
  {/if}
</Page>

<style>
  .find {
    width: 100%;
    margin-bottom: 12px;
  }
  .chat-list {
    padding: 0;
    overflow: hidden;
    max-width: 760px;
  }
  .chat-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  .chat-row:last-child {
    border-bottom: 0;
  }
  .chat-row:hover {
    background: var(--surface2);
    text-decoration: none;
  }
  .chat-title {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    font-size: 15px;
    line-height: 1.35;
    color: var(--fg-bright);
  }
  .chat-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--muted);
  }
  .chat-proj {
    padding: 1px 8px;
    border-radius: 999px;
    background: var(--surface2);
    color: var(--fg-dim);
    font-size: 12px;
  }
  .none {
    color: var(--muted);
    font-size: 14px;
  }
</style>
