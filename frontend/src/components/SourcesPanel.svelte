<script lang="ts">
  // Where an answer came from: one row per source, domain first, the page
  // title (or path) under it. Cited sources carry the text's own link; fetched
  // ones are pages the agent read but didn't cite.
  import type { SourceRef } from '../lib/types'
  import Icon from './Icon.svelte'

  let { sources, compact = false }: { sources: SourceRef[]; compact?: boolean } = $props()

  function pathOf(url: string | null): string {
    if (!url) return ''
    try {
      const u = new URL(url)
      const p = decodeURIComponent(u.pathname).replace(/\/+$/, '')
      return p.length > 1 ? p : ''
    } catch {
      return ''
    }
  }
  function label(s: SourceRef): string {
    if (s.title) return s.title
    const p = pathOf(s.url)
    return p ? p.split('/').filter(Boolean).slice(-2).join(' / ') : ''
  }
</script>

{#if sources.length}
  <ol class="sources" class:compact>
    {#each sources as s, i (s.url ?? 'dom:' + s.domain)}
      <li class="source">
        <span class="src-n num">{i + 1}</span>
        {#if s.url}
          <a class="src-body" href={s.url} target="_blank" rel="noopener noreferrer" title={s.url}>
            <span class="src-domain"><Icon name="link" size={11} />{s.domain}</span>
            {#if label(s)}<span class="src-title">{label(s)}</span>{/if}
          </a>
        {:else}
          <span class="src-body" title="Cited by domain only; no link was given">
            <span class="src-domain"><Icon name="link" size={11} />{s.domain}</span>
          </span>
        {/if}
        {#if s.via === 'fetched'}<span class="src-via" title="Read by the agent but not cited">read</span
          >{/if}
      </li>
    {/each}
  </ol>
{:else}
  <p class="src-empty">No sources yet.</p>
{/if}

<style>
  .sources {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .source {
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr) auto;
    gap: 6px;
    align-items: start;
    font-size: 12.5px;
  }
  .src-n {
    font-size: 11px;
    color: var(--muted);
    padding-top: 2px;
    text-align: right;
  }
  .src-body {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
    color: inherit;
    text-decoration: none;
    padding: 4px 6px;
    margin: -4px -6px;
    border-radius: 6px;
  }
  a.src-body:hover {
    background: var(--surface2);
    text-decoration: none;
  }
  .src-domain {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-weight: 500;
    color: var(--fg);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .src-title {
    color: var(--fg-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .compact .src-title {
    display: none;
  }
  .src-via {
    font-size: 11px;
    color: var(--muted);
    padding-top: 2px;
  }
  .src-empty {
    font-size: 12.5px;
    color: var(--muted);
  }
</style>
