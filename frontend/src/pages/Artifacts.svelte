<script lang="ts">
  // Artifacts: everything your agents wrote down, in one place — the documents
  // they saved and the files in your projects — instead of one folder per
  // project. Click one and it opens beside the list.
  import { api } from '../lib/api'
  import { onReturn } from '../lib/refresh'
  import { href, link, navigate } from '../lib/router.svelte'
  import { relativeTime } from '../lib/time'
  import {
    GROUPS,
    displayTitle,
    filterLibrary,
    groupCounts,
    groupLabel,
    groupOf,
    libraryProjects,
    type LibraryGroup,
  } from '../lib/library'
  import Page from '../components/Page.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import EmptyState from '../components/EmptyState.svelte'
  import DocPanel from '../components/DocPanel.svelte'
  import Icon from '../components/Icon.svelte'
  import type { LibraryItem } from '../lib/types'

  let items = $state<LibraryItem[]>([])
  let loaded = $state(false)
  let error = $state<string | null>(null)

  let q = $state('')
  let group = $state<LibraryGroup | 'all'>('all')
  let project = $state('')
  let openUrl = $state<string | null>(null)

  const scope = $derived(libraryProjects(items))
  const counts = $derived(groupCounts(items, { q, project }))
  const shown = $derived(filterLibrary(items, { q, group, project }))
  // Only offer the kinds you actually have.
  const groups = $derived(GROUPS.filter((g) => groupCounts(items, { q: '', project: '' })[g] > 0))
  const open = $derived(openUrl ? (items.find((i) => i.url === openUrl) ?? null) : null)

  const ICON: Record<LibraryGroup, string> = {
    page: 'file-text',
    pdf: 'book',
    note: 'list',
    image: 'image',
    other: 'file',
  }
  const when = (it: LibraryItem) => relativeTime(new Date(it.modified * 1000).toISOString())

  // "Ask" starts a chat about the document, in its project so the agent can
  // read the file.
  function ask(it: LibraryItem) {
    const project = it.project_id ? `project=${encodeURIComponent(it.project_id)}&` : ''
    navigate(`/chat?${project}draft=${encodeURIComponent(`In ${it.name}: `)}`)
  }

  let seq = 0
  async function load() {
    const mine = ++seq
    try {
      const next = await api.library()
      if (mine !== seq) return
      items = next
      error = null
    } catch (e) {
      if (mine !== seq) return
      error = String(e)
    } finally {
      if (mine === seq) loaded = true
    }
  }
  $effect(() => {
    void load()
  })
  $effect(() => onReturn(() => void load()))
</script>

<Page title="Artifacts">
  {#snippet subtitle()}Documents your agents saved and the files in your projects{/snippet}

  <div class="lib" class:with-doc={!!open}>
    {#if error}<ErrorBanner {error} onretry={load} />{/if}
    {#if !loaded}
      <Skeleton rows={5} />
    {:else if !items.length}
      <EmptyState
        title="Nothing saved yet"
        hint="Ask an agent to save a document, or add files to a project, and they will be listed here."
        icon="layers"
      >
        <a href={href('/chat')} use:link class="btn-primary">
          <Icon name="plus" size={14} stroke={2.2} />New chat
        </a>
      </EmptyState>
    {:else}
      <div class="lib-controls">
        <div class="search-wrap">
          <Icon name="search" size={14} class="search-ico" />
          <input
            class="field lib-search"
            type="search"
            bind:value={q}
            placeholder="Search artifacts"
            aria-label="Search artifacts"
          />
        </div>
        {#if scope.projects.length + (scope.loose ? 1 : 0) > 1}
          <select class="field lib-project" bind:value={project} aria-label="Project">
            <option value="">All projects</option>
            {#each scope.projects as p (p.id)}
              <option value={p.id}>{p.name}</option>
            {/each}
            {#if scope.loose}<option value="none">No project</option>{/if}
          </select>
        {/if}
        <div class="seg" role="group" aria-label="Kind">
          <button
            class="seg-btn"
            class:on={group === 'all'}
            aria-pressed={group === 'all'}
            onclick={() => (group = 'all')}>All <span class="seg-n">{counts.all}</span></button
          >
          {#each groups as g (g)}
            <button
              class="seg-btn"
              class:on={group === g}
              aria-pressed={group === g}
              onclick={() => (group = g)}>{groupLabel(g)} <span class="seg-n">{counts[g]}</span></button
            >
          {/each}
        </div>
      </div>

      {#if !shown.length}
        <EmptyState title="No matches" hint="Nothing here matches those filters." icon="search" />
      {:else}
        <ul class="lib-grid">
          {#each shown as it (it.url)}
            {@const g = groupOf(it.name)}
            <li>
              <button
                class="card lib-card"
                class:on={openUrl === it.url}
                aria-pressed={openUrl === it.url}
                onclick={() => (openUrl = openUrl === it.url ? null : it.url)}
              >
                <span class="lib-ico kind-{g}"><Icon name={ICON[g]} size={16} /></span>
                <span class="lib-text">
                  <span class="lib-title">{displayTitle(it)}</span>
                  <span class="lib-file">{it.name}</span>
                  <span class="lib-meta">
                    <span class="lib-proj" class:loose={!it.project_id}
                      >{it.project_name ?? 'No project'}</span
                    >
                    <span>{when(it)}</span>
                    <span>{it.size_human}</span>
                  </span>
                </span>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  </div>
</Page>

{#if open}
  <div class="lib-doc">
    <DocPanel
      name={open.name}
      title={displayTitle(open)}
      url={open.url}
      onclose={() => (openUrl = null)}
      onask={() => ask(open)}
    />
  </div>
{/if}

<style>
  /* The open document is a fixed column on the right; the list keeps its
     place and reflows into the space that is left. */
  .lib.with-doc {
    margin-right: min(54vw, 800px);
  }
  .lib-doc {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 70;
    width: min(54vw, 800px);
    padding: 10px 12px 10px 0;
  }
  @media (max-width: 1100px) {
    .lib.with-doc {
      margin-right: 0;
    }
    .lib-doc {
      inset: 0;
      width: auto;
      z-index: 120;
      padding: calc(8px + var(--safe-top)) 8px calc(8px + var(--safe-bottom));
      background: var(--backdrop);
    }
  }

  .lib-controls {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 18px;
  }
  .search-wrap {
    position: relative;
    width: min(280px, 100%);
  }
  .search-wrap :global(.search-ico) {
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--muted);
    pointer-events: none;
  }
  .search-wrap input.lib-search.field {
    padding-left: 32px;
  }
  .lib-project {
    width: auto;
    max-width: 220px;
  }
  .seg {
    margin-left: auto;
  }

  .lib-grid {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(270px, 1fr));
    gap: 10px;
  }
  .lib-card {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    width: 100%;
    height: 100%;
    padding: 13px 14px;
    text-align: left;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }
  .lib-card:hover {
    border-color: var(--border2);
    background: color-mix(in srgb, var(--surface) 70%, var(--surface2));
  }
  .lib-card.on {
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--accent-glow);
  }
  .lib-ico {
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    flex-shrink: 0;
    border-radius: 9px;
    background: var(--surface2);
    color: var(--fg-dim);
  }
  .lib-ico.kind-page {
    background: var(--accent-glow);
    color: var(--accent-soft);
  }
  .lib-text {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    flex: 1;
  }
  .lib-title {
    font-size: 14px;
    font-weight: 600;
    line-height: 1.3;
    color: var(--fg-bright);
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
    overflow-wrap: anywhere;
  }
  .lib-file {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .lib-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
    min-width: 0;
  }
  .lib-proj {
    padding: 1px 7px;
    border-radius: 5px;
    background: var(--surface2);
    color: var(--fg-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }
  .lib-proj.loose {
    background: transparent;
    padding: 0;
    color: var(--muted);
  }
</style>
