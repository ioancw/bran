<script lang="ts">
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { renderMarkdown } from '../lib/markdown'
  import type { BriefingSummary } from '../lib/types'

  let { name }: { name: string | null } = $props()

  let list = $state<BriefingSummary[]>([])
  let content = $state<string | null>(null)
  let error = $state<string | null>(null)

  $effect(() => {
    error = null
    content = null
    if (name) {
      void api.briefing(name).then((b) => (content = b.content)).catch((e) => (error = String(e)))
    } else {
      void api.briefings().then((b) => (list = b)).catch((e) => (error = String(e)))
    }
  })
</script>

<header class="page-header">
  <h1>Briefings</h1>
  <span class="subheading">{name ?? 'saved digests'}</span>
</header>
<div class="px-8 py-6">
  {#if error}<div class="card" style="color: var(--red);">{error}</div>{/if}
  {#if name}
    <a href={href('/briefings')} use:link class="btn-outline" style="text-decoration: none;">← all briefings</a>
    {#if content != null}
      <div class="card" style="margin-top: 16px;"><div class="prose-md">{@html renderMarkdown(content)}</div></div>
    {/if}
  {:else if !list.length}
    <div class="empty-state"><h3>no briefings yet</h3></div>
  {:else}
    <div class="space-y-2">
      {#each list as b}
        <a href={href('/briefings/' + encodeURIComponent(b.name))} use:link class="card" style="display: flex; gap: 12px; align-items: baseline; text-decoration: none;">
          <span class="text-bright">{b.name}</span>
          <span class="ml-auto label-cap">{b.size_kb} kb</span>
        </a>
      {/each}
    </div>
  {/if}
</div>
