<script lang="ts">
  // The reading view of a report: what a notification opens on a phone, and
  // what "read the brief" should feel like on a desk. A headline, when it was
  // written and whether it was checked, where it came from, then the report
  // itself in a reading column — nothing about turns, cost or tools (that is
  // the run page, one link away). Older and newer editions of the same runner
  // are a tap either side.
  import { api } from '../lib/api'
  import { navigate, href, link, router } from '../lib/router.svelte'
  import { bumpActivity } from '../lib/workspace.svelte'
  import { backLink, dateline, readMinutes, splitHeadline } from '../lib/reading'
  import { isAlertResult, isSuperseded, verificationOf, verifyBadge } from '../lib/verification'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Prose from '../components/Prose.svelte'
  import Icon from '../components/Icon.svelte'
  import SourceIcon from '../components/SourceIcon.svelte'
  import SourcesPanel from '../components/SourcesPanel.svelte'
  import type { RunRecord, SourceRef } from '../lib/types'

  let { runId }: { runId: string } = $props()

  let run = $state<RunRecord | null>(null)
  let sources = $state<SourceRef[]>([])
  let runnerName = $state('')
  // Readable editions of the same runner, newest first.
  let editions = $state<RunRecord[]>([])
  let error = $state<unknown>(null)
  let reloadTick = $state(0)

  $effect(() => {
    void reloadTick
    const id = runId
    let live = true
    run = null
    sources = []
    runnerName = ''
    editions = []
    error = null
    void (async () => {
      let rec: RunRecord
      try {
        rec = await api.run(id)
      } catch (e) {
        if (live) error = e
        return
      }
      if (!live) return
      run = rec
      api
        .runSources(id)
        .then((s) => {
          if (live) sources = s
        })
        .catch(() => {})
      if (rec.schedule_id) {
        const sid = rec.schedule_id
        api
          .schedules()
          .then((all) => {
            if (live) runnerName = all.find((s) => s.id === sid)?.name ?? ''
          })
          .catch(() => {})
        api
          .runs({ schedule_id: sid, limit: 60 })
          .then((rows) => {
            if (!live) return
            editions = rows.filter(
              (r) => r.status === 'completed' && (r.result ?? '').trim() && !isSuperseded(r),
            )
          })
          .catch(() => {})
      }
      if (rec.status === 'completed' && !rec.read_at) {
        api
          .markOutputsRead([id])
          .then(() => bumpActivity())
          .catch(() => {})
      }
    })()
    return () => {
      live = false
    }
  })

  const parts = $derived(splitHeadline(run?.result ?? ''))
  const headline = $derived(parts.headline || runnerName || run?.agent || 'Report')
  const kicker = $derived(
    parts.headline ? runnerName || run?.agent || '' : runnerName ? (run?.agent ?? '') : '',
  )
  const badge = $derived(run ? verifyBadge(run) : null)
  const verification = $derived(run ? verificationOf(run) : null)
  const position = $derived(editions.findIndex((r) => r.id === runId))
  const newer = $derived(position > 0 ? editions[position - 1] : null)
  const older = $derived(position >= 0 && position < editions.length - 1 ? editions[position + 1] : null)
  // A report draws on dozens of pages; what a reader wants first is which
  // sites, and how heavily. The full list is one click below.
  const sites = $derived.by(() => {
    const count: Record<string, number> = {}
    for (const s of sources) count[s.domain] = (count[s.domain] ?? 0) + 1
    return Object.entries(count)
      .map(([domain, n]) => ({ domain, n }))
      .sort((a, b) => b.n - a.n)
  })
  const readable = $derived(run?.status === 'completed' && !!(run.result ?? '').trim())

  // Back goes to the page the reader came from. A teammate's chat is named.
  let chatName = $state('')
  $effect(() => {
    const m = /^\/chat\/([^/?]+)/.exec(router.from ?? '')
    chatName = ''
    if (!m) return
    let live = true
    api
      .teammates()
      .then((all) => {
        if (live) chatName = all.find((t) => t.chat_id === m[1])?.name ?? ''
      })
      .catch(() => {})
    return () => {
      live = false
    }
  })
  const back = $derived(backLink(router.from, chatName))

  // The reviewer's notes sit folded after the report, so the report leads. The
  // badge under the headline is the way to them.
  let notes = $state<HTMLDetailsElement | null>(null)
  function showNotes() {
    if (!notes) return
    notes.open = true
    notes.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function discuss() {
    if (!run) return
    navigate('/chat?draft=' + encodeURIComponent(`Let's discuss the output of run ${run.id} (${headline}). `))
  }
</script>

<Page>
  {#snippet head()}
    <a href={href(back.to)} use:link class="back tap"><Icon name="arrow-left" size={14} />{back.label}</a>
    <div class="ml-auto editions">
      {#if older}
        <a
          href={href('/read/' + older.id)}
          use:link
          class="btn-ghost btn-sm"
          title="The edition before this one"
        >
          <Icon name="chevron-left" size={14} />Earlier
        </a>
      {/if}
      {#if newer}
        <a
          href={href('/read/' + newer.id)}
          use:link
          class="btn-ghost btn-sm"
          title="The edition after this one"
        >
          Later<Icon name="chevron-right" size={14} />
        </a>
      {/if}
    </div>
  {/snippet}

  {#if error}
    <div class="reader"><ErrorBanner {error} onretry={() => reloadTick++} /></div>
  {:else if !run}
    <div class="reader"><Skeleton rows={8} /></div>
  {:else if !readable}
    <div class="reader">
      <h1 class="headline">{headline}</h1>
      <p class="state">
        {#if run.status === 'running' || run.status === 'pending'}
          This run is still going; the report will be here when it finishes.
        {:else if run.status === 'completed'}
          This run finished without writing a report.
        {:else}
          {run.error_summary?.title ?? 'This run did not finish.'}
          {run.error_summary?.hint ?? ''}
        {/if}
      </p>
      <a href={href('/runs/' + run.id)} use:link class="btn-outline">Open the run</a>
    </div>
  {:else}
    <article class="reader">
      {#if kicker}<div class="kicker">{kicker}</div>{/if}
      <h1 class="headline">{headline}</h1>
      <div class="dateline">
        <span>{dateline(run.ended_at ?? run.started_at)}</span>
        <span>·</span>
        <span>{readMinutes(parts.body)} min read</span>
        {#if badge?.tone === 'note'}
          <button type="button" class="badge note tap" title={badge.hint} onclick={showNotes}>
            <Icon name="message-square" size={12} />{badge.label}
          </button>
        {:else if badge}
          <span class="badge {badge.tone}" title={badge.hint}>
            <Icon name={badge.tone === 'ok' ? 'shield-check' : 'alert-triangle'} size={12} />{badge.label}
          </span>
        {/if}
        {#if isAlertResult(run)}
          <span class="badge alert"><Icon name="bell" size={12} />Alert</span>
        {/if}
        <a
          class="btn-ghost btn-sm save"
          href={'/spa/runs/' + encodeURIComponent(run.id) + '/export.html'}
          download
          title="Save this report as one HTML file: it opens anywhere and prints to PDF"
          ><Icon name="download" size={13} />Download</a
        >
      </div>

      {#if sites.length}
        <details class="sites">
          <summary>
            <span class="sites-row">
              {#each sites.slice(0, 8) as s (s.domain)}
                <span class="site"
                  ><SourceIcon domain={s.domain} size={14} />{s.domain}{#if s.n > 1}<span class="site-n"
                      >{s.n}</span
                    >{/if}</span
                >
              {/each}
              {#if sites.length > 8}<span class="site more">+{sites.length - 8} more</span>{/if}
            </span>
            <span class="sites-all">All {sources.length} sources<Icon name="chevron-down" size={13} /></span>
          </summary>
          <div class="sites-list"><SourcesPanel {sources} /></div>
        </details>
      {/if}

      <div class="body"><Prose text={parts.body} {sources} /></div>

      {#if verification?.status === 'done' && verification.pass === false && verification.issues?.length}
        <details class="issues" bind:this={notes}>
          <summary>
            <span
              >Reviewer notes: {verification.issues.length}
              {verification.issues.length === 1 ? 'point' : 'points'} raised about this report</span
            ><Icon name="chevron-down" size={14} />
          </summary>
          <ul>
            {#each verification.issues as issue, i (i)}<li>{issue}</li>{/each}
          </ul>
        </details>
      {/if}

      <footer class="foot">
        <button class="btn-outline" onclick={discuss}
          ><Icon name="message-square" size={14} />Discuss in chat</button
        >
        <a href={href('/runs/' + run.id)} use:link class="btn-ghost">How it was made</a>
        {#if older}
          <a href={href('/read/' + older.id)} use:link class="btn-ghost ml-auto">
            <Icon name="chevron-left" size={14} />Earlier edition
          </a>
        {/if}
      </footer>
    </article>
  {/if}
</Page>

<style>
  .back {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-dim);
  }
  .back:hover {
    color: var(--fg-bright);
    text-decoration: none;
  }
  .editions {
    display: flex;
    gap: 2px;
  }
  .reader {
    max-width: 720px;
    margin: 0 auto;
    padding: 20px 0 48px;
  }
  .kicker {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--accent-soft);
    margin-bottom: 8px;
  }
  .headline {
    font-family: var(--font-prose);
    font-size: 32px;
    line-height: 1.18;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--fg-bright);
    margin: 0;
    text-wrap: balance;
  }
  .dateline {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 8px;
    margin: 12px 0 18px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    color: var(--muted);
  }
  .sites summary {
    display: flex;
    flex-direction: column;
    gap: 8px;
    list-style: none;
    cursor: pointer;
  }
  .sites summary::-webkit-details-marker {
    display: none;
  }
  .sites-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .site {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 9px 3px 7px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
    font-size: 13px;
    color: var(--fg-dim);
  }
  .site.more {
    padding-left: 9px;
    color: var(--muted);
  }
  .site-n {
    font-size: 11px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .sites-all {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    align-self: flex-start;
    font-size: 13px;
    font-weight: 500;
    color: var(--muted);
  }
  .sites summary:hover .sites-all {
    color: var(--fg-bright);
  }
  .sites[open] .sites-all :global(.icon) {
    transform: rotate(180deg);
  }
  .sites-list {
    margin-top: 12px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    background: var(--surface);
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge.ok {
    background: var(--green-soft);
    color: var(--green);
  }
  .badge.warn {
    background: var(--amber-soft);
    color: var(--amber);
  }
  .badge.alert {
    background: var(--red-soft);
    color: var(--red);
  }
  .badge.note {
    border: 0;
    background: var(--surface2);
    color: var(--fg-dim);
    font-family: inherit;
    cursor: pointer;
  }
  .badge.note:hover {
    color: var(--fg-bright);
  }
  .issues {
    margin-top: 32px;
    padding: 4px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    font-size: 14px;
    line-height: 1.55;
    color: var(--fg-dim);
    scroll-margin-top: 72px;
  }
  .issues + .foot {
    margin-top: 16px;
  }
  .save {
    margin-left: auto;
    text-decoration: none;
  }
  .issues summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    list-style: none;
    cursor: pointer;
    min-height: 40px;
    font-weight: 500;
    color: var(--fg-dim);
  }
  .issues summary::-webkit-details-marker {
    display: none;
  }
  .issues[open] summary {
    margin-bottom: 4px;
  }
  .issues[open] summary :global(.icon) {
    transform: rotate(180deg);
  }
  @media (max-width: 768px) {
    /* The count line is enough until it is opened. */
    .sites-row {
      display: none;
    }
  }
  .issues ul {
    margin: 0;
    padding: 0 0 10px 18px;
  }
  .body {
    margin-top: 18px;
  }
  .body :global(.prose-md) {
    font-family: var(--font-prose);
    font-size: 17px;
    line-height: 1.72;
  }
  .state {
    margin: 14px 0 18px;
    font-size: 15px;
    line-height: 1.6;
    color: var(--fg-dim);
  }
  .foot {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 36px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }
  @media (max-width: 768px) {
    .reader {
      padding: 8px 0 40px;
    }
    .headline {
      font-size: 26px;
    }
    .body :global(.prose-md) {
      font-size: 17px;
      line-height: 1.68;
    }
  }
</style>
