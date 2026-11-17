<script lang="ts">
  // The sources of one answer as a row of cards above it — site icon, domain,
  // page title — numbered to match the citation chips in the text. Shows the
  // first few; the rest are one click away.
  import type { SourceRef } from '../lib/types'
  import { pathLabel } from '../chat/sources'
  import SourceIcon from './SourceIcon.svelte'

  let { sources, max = 4 }: { sources: SourceRef[]; max?: number } = $props()

  let all = $state(false)
  // One row: every source when they fit, else all but the last slot, which
  // becomes "+N more".
  const shown = $derived(all || sources.length <= max ? sources : sources.slice(0, max - 1))
  const hidden = $derived(sources.length - shown.length)
  const label = (s: SourceRef) => s.title || pathLabel(s.url) || s.domain
</script>

{#if sources.length}
  <div class="strip" role="group" aria-label="Sources">
    {#each shown as s, i (s.url ?? 'dom:' + s.domain)}
      {#if s.url}
        <a class="src-card" href={s.url} target="_blank" rel="noopener noreferrer" title={s.url}>
          <span class="src-head">
            <SourceIcon domain={s.domain} size={14} />
            <span class="src-domain">{s.domain}</span>
            <span class="src-n">{i + 1}</span>
          </span>
          <span class="src-title">{label(s)}</span>
        </a>
      {:else}
        <span class="src-card" title="Cited by name only; no link was given">
          <span class="src-head">
            <SourceIcon domain={s.domain} size={14} />
            <span class="src-domain">{s.domain}</span>
            <span class="src-n">{i + 1}</span>
          </span>
          <span class="src-title">Cited by name</span>
        </span>
      {/if}
    {/each}
    {#if hidden > 0}
      <button class="src-card more" onclick={() => (all = true)} aria-label="Show {hidden} more sources">
        <span class="more-icons">
          {#each sources.slice(max - 1, max + 2) as s (s.url ?? 'dom:' + s.domain)}
            <SourceIcon domain={s.domain} size={14} />
          {/each}
        </span>
        <span class="src-title">+{hidden} more</span>
      </button>
    {/if}
  </div>
{/if}

<style>
  .strip {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 8px;
    margin: 10px 0 2px;
  }
  .src-card {
    display: flex;
    flex-direction: column;
    gap: 5px;
    min-width: 0;
    padding: 8px 10px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    color: inherit;
    text-decoration: none;
    text-align: left;
    font: inherit;
    animation: src-in var(--dur-3) var(--transition) both;
    transition:
      background var(--dur-1) var(--transition),
      border-color var(--dur-1) var(--transition);
  }
  a.src-card:hover,
  button.src-card:hover {
    background: var(--surface2);
    border-color: var(--border2);
    text-decoration: none;
  }
  button.src-card {
    cursor: pointer;
  }
  .src-head {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }
  .src-domain {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    color: var(--muted);
  }
  .src-n {
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .src-title {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    font-size: 13px;
    font-weight: 500;
    line-height: 1.35;
    color: var(--fg);
    overflow-wrap: anywhere;
  }
  .more {
    justify-content: center;
  }
  .more-icons {
    display: flex;
    gap: 4px;
  }
  @keyframes src-in {
    from {
      opacity: 0;
      transform: translateY(3px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  /* Phones: one swipeable row instead of a tall grid above the answer. */
  @media (max-width: 768px) {
    .strip {
      display: flex;
      overflow-x: auto;
      scroll-snap-type: x proximity;
      padding-bottom: 4px;
    }
    .src-card {
      flex: 0 0 160px;
      scroll-snap-align: start;
    }
  }
</style>
