<script lang="ts">
  // A report one of the teammate's automations delivered, shown in its chat
  // where it landed. Reading opens the whole report; replying puts a message
  // about it in the composer (the teammate reads the full report first).
  import Icon from '../components/Icon.svelte'
  import { href, link } from '../lib/router.svelte'
  import { clock, fullStamp } from './format'

  let {
    runId,
    title,
    excerpt,
    schedule,
    at,
    onreply,
  }: {
    runId: string
    title: string
    excerpt: string
    schedule: string | null
    at: string
    onreply?: (text: string) => void
  } = $props()
</script>

<article class="card delivery">
  <header class="delivery-head">
    <Icon name="file-text" size={14} />
    <span>{schedule ? `Delivered by ${schedule}` : 'Delivered'}</span>
    <span class="stamp ml-auto" title={fullStamp(at)}>{clock(at)}</span>
  </header>
  <a href={href('/read/' + runId)} use:link class="delivery-title">{title}</a>
  {#if excerpt}<p class="delivery-excerpt">{excerpt}</p>{/if}
  <footer class="delivery-foot">
    <a href={href('/read/' + runId)} use:link class="btn-outline btn-sm"
      ><Icon name="book-open" size={13} />Read</a
    >
    {#if onreply}
      <button class="btn-ghost btn-sm" onclick={() => onreply?.(`About "${title}": `)}>
        <Icon name="corner-down-right" size={13} />Reply
      </button>
    {/if}
  </footer>
</article>

<style>
  .delivery {
    max-width: 640px;
  }
  .delivery-head {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .delivery-title {
    display: block;
    font-size: 15px;
    font-weight: 600;
    color: var(--fg-bright);
    text-decoration: none;
  }
  .delivery-title:hover {
    text-decoration: underline;
  }
  .delivery-excerpt {
    margin: 4px 0 0;
    font-size: 13px;
    color: var(--fg-dim);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .delivery-foot {
    display: flex;
    gap: 8px;
    margin-top: 10px;
  }
</style>
