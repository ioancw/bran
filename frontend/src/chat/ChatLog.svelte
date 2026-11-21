<script lang="ts">
  // Renders a list of ChatItems — the one render path for both live chat and
  // transcript replay. `streamingIndex` marks the assistant bubble that should
  // show a blinking cursor (live only).
  import {
    groupRows,
    askedRequestId,
    type ChatItem,
    type Row,
    SPAWN_TOOL,
    PLAN_TOOL,
    DOC_TOOL,
    ASK_TOOL,
    isDelegation,
  } from './events'
  import { clock, fullStamp, dayKey, dayLabel } from './format'
  import { sourcesInRange, turnRanges } from './sources'
  import { turnStats, turnSummary } from './activity'
  import { answerKey } from './turn'
  import Prose from '../components/Prose.svelte'
  import Icon from '../components/Icon.svelte'
  import SourceStrip from '../components/SourceStrip.svelte'
  import ToolBlock from './ToolBlock.svelte'
  import Activity from './Activity.svelte'
  import SpawnCard from './SpawnCard.svelte'
  import SubagentCard from './SubagentCard.svelte'
  import PlanCard from './PlanCard.svelte'
  import DocCard from './DocCard.svelte'
  import DeliveryCard from './DeliveryCard.svelte'
  import RequestCard from './RequestCard.svelte'
  import { toast } from '../lib/toast.svelte'
  import type { SourceRef, TeammateRequest } from '../lib/types'

  let {
    items,
    streamingIndex = -1,
    busy = false,
    onaction,
    onretry,
    sources = [],
    strips = false,
    feedback = {},
    onfeedback,
    onregenerate,
    onrunner,
    onopendoc,
    requests = {},
    onanswer,
  }: {
    items: ChatItem[]
    streamingIndex?: number
    // A turn is in flight (live chat only): its newest activity row is live,
    // and its answer has no actions yet.
    busy?: boolean
    // Lets interactive cards (PlanCard approve/revise) and the user-message
    // "Edit" action put a message in the composer (and optionally send it).
    // Absent on read-only surfaces.
    onaction?: (text: string, sendNow: boolean) => void
    // Re-send the turn that produced a trailing error row. Only the live chat
    // passes this; transcript replays render errors as plain records.
    onretry?: () => void
    // What the server knows about the conversation's sources (titles, and the
    // page behind a domain-only citation). Each turn's own list is worked out
    // from its items (chat/sources.ts).
    sources?: SourceRef[]
    // Show each turn's sources as a row of cards above its answer.
    strips?: boolean
    // Thumbs up/down: answer key -> 1 / -1, and the handler that records one.
    feedback?: Record<string, number>
    onfeedback?: (key: string, rating: number, prompt: string, answer: string) => void
    // Ask the last question again / turn it into a scheduled runner.
    onregenerate?: () => void
    onrunner?: (prompt: string) => void
    // Open a document the agent saved, beside the chat.
    onopendoc?: (name: string, title: string) => void
    // A teammate's chat: what it asked before acting (by request id), and the
    // handler that records the user's answer.
    requests?: Record<string, TeammateRequest>
    onanswer?: (id: string, decision: 'approve' | 'decline', note: string) => Promise<void>
  } = $props()

  // Copy a turn's text; brief "Copied" feedback inline.
  let copiedIdx = $state(-1)
  let copiedTimer: ReturnType<typeof setTimeout> | undefined
  async function copyText(text: string, i: number) {
    try {
      await navigator.clipboard.writeText(text)
      copiedIdx = i
      clearTimeout(copiedTimer)
      copiedTimer = setTimeout(() => (copiedIdx = -1), 2000)
    } catch {
      toast('Copy failed', 'err')
    }
  }

  const rows = $derived(groupRows(items))

  // Stable per-row key so expansion state (an activity row the user opened
  // mid-stream) stays attached to its row as later events extend the list.
  function rowKey(row: Row): string {
    if (row.group) return 'g:' + row.idx
    if (row.item.kind === 'tool' && row.item.toolId) return 't:' + row.item.toolId
    return 'i:' + row.idx
  }

  // Day dividers: emitted before a row whose calendar day differs from the
  // last timestamped row before it. Rows without a timestamp never divide.
  function stampOf(row: Row): string | undefined {
    if (row.group) return undefined
    const it = row.item
    return it.kind === 'user' || it.kind === 'assistant' || it.kind === 'delivery' || it.kind === 'request'
      ? it.at
      : undefined
  }
  const dividers = $derived.by(() => {
    const out: Record<number, true> = {}
    let lastDay: string | null = null
    rows.forEach((row, i) => {
      const k = dayKey(stampOf(row))
      if (!k) return
      if (k !== lastDay) out[i] = true
      lastDay = k
    })
    return out
  })

  // --- Turns: a user message and everything up to the next one ---------------
  const turns = $derived(turnRanges(items))
  // Item index -> index of its turn.
  const turnOf = $derived.by(() => {
    const out: number[] = new Array(items.length).fill(0)
    turns.forEach((t, k) => {
      for (let i = t.start; i < t.end; i++) out[i] = k
    })
    return out
  })
  // Each turn's sources. A finished turn never changes, so only the newest
  // one is worked out again as a reply streams in.
  // eslint-disable-next-line svelte/prefer-svelte-reactivity -- a cache, never read by the template
  let memo = new Map<string, SourceRef[]>()
  let memoItems: ChatItem[] | null = null
  let memoKnown: SourceRef[] | null = null
  const turnSources = $derived.by(() => {
    if (memoItems !== items || memoKnown !== sources) {
      memo = new Map()
      memoItems = items
      memoKnown = sources
    }
    return turns.map((t, k) => {
      const newest = k === turns.length - 1
      const key = t.start + ':' + t.end
      const hit = newest ? undefined : memo.get(key)
      if (hit) return hit
      const found = sourcesInRange(items, t.start, t.end, sources)
      if (!newest) memo.set(key, found)
      return found
    })
  })
  // The answer of a turn is its last piece of prose: that is the one that
  // carries the actions (rate, ask again, make it a runner).
  const answerIdx = $derived(
    turns.map((t) => {
      for (let i = t.end - 1; i >= t.start; i--) if (items[i].kind === 'assistant') return i
      return -1
    }),
  )
  function promptOf(k: number): string {
    const first = items[turns[k]?.start]
    return first?.kind === 'user' ? first.text : ''
  }
  function footerLine(i: number, cost: number | null): string {
    const k = turnOf[i]
    const t = turns[k]
    return turnSummary(turnStats(items, t.start, t.end), turnSources[k].length, cost)
  }
  function rate(k: number, text: string, rating: number) {
    const key = answerKey(text)
    onfeedback?.(key, feedback[key] === rating ? 0 : rating, promptOf(k), text)
  }
</script>

{#each rows as row, ri (rowKey(row))}
  {#if dividers[ri]}
    <div class="day-divider" role="separator">
      <span>{dayLabel(stampOf(row)!)}</span>
    </div>
  {/if}
  <div class="chat-row">
    {#if row.group}
      <Activity steps={row.steps} live={busy && ri === rows.length - 1} />
    {:else}
      {@const item = row.item}
      {@const i = row.idx}
      {@const k = turnOf[i]}
      {#if item.kind === 'user'}
        <!-- Your turn: a compact right-aligned bubble (position says who it is). -->
        <div class="msg-user">
          <div class="user-bubble">{item.text}</div>
          <div class="row-actions user-actions">
            {#if item.at}<span class="stamp" title={fullStamp(item.at)}>{clock(item.at)}</span>{/if}
            <button
              class="msg-action-btn"
              class:done={copiedIdx === i}
              onclick={() => copyText(item.text, i)}
            >
              {#if copiedIdx === i}<Icon name="check" size={13} />Copied{:else}<Icon
                  name="copy"
                  size={13}
                />Copy{/if}
            </button>
            {#if onaction}
              <button
                class="msg-action-btn"
                title="Put this message back in the composer"
                onclick={() => onaction?.(item.text, false)}
              >
                <Icon name="pencil" size={13} />Edit
              </button>
            {/if}
          </div>
        </div>
        {#if strips && turnSources[k]?.length}
          <SourceStrip sources={turnSources[k]} />
        {/if}
      {:else if item.kind === 'assistant'}
        <!-- bran's turn: prose flows on the canvas, no card chrome — the answer is
         the page, not a boxed message. Actions reveal on hover/focus. -->
        {@const isAnswer = answerIdx[k] === i}
        {@const settled = !(busy && k === turns.length - 1)}
        {@const rating = feedback[answerKey(item.text)] ?? 0}
        <div class="msg-assistant" class:rated={rating !== 0}>
          <Prose
            text={item.text}
            streaming={i === streamingIndex}
            class="reading"
            sources={turnSources[k] ?? []}
          />
          {#if i !== streamingIndex}
            <div class="row-actions">
              <button
                class="msg-action-btn"
                class:done={copiedIdx === i}
                onclick={() => copyText(item.text, i)}
              >
                {#if copiedIdx === i}<Icon name="check" size={13} />Copied{:else}<Icon
                    name="copy"
                    size={13}
                  />Copy{/if}
              </button>
              {#if isAnswer && settled}
                {#if onfeedback}
                  <button
                    class="msg-action-btn icon-only"
                    class:on={rating > 0}
                    aria-pressed={rating > 0}
                    aria-label="Good answer"
                    title="Good answer"
                    onclick={() => rate(k, item.text, 1)}><Icon name="thumbs-up" size={13} /></button
                  >
                  <button
                    class="msg-action-btn icon-only"
                    class:on={rating < 0}
                    aria-pressed={rating < 0}
                    aria-label="Poor answer"
                    title="Poor answer"
                    onclick={() => rate(k, item.text, -1)}><Icon name="thumbs-down" size={13} /></button
                  >
                {/if}
                {#if k === turns.length - 1 && promptOf(k)}
                  {#if onregenerate}
                    <button
                      class="msg-action-btn"
                      title="Ask the same question again"
                      onclick={() => onregenerate?.()}><Icon name="refresh" size={13} />Ask again</button
                    >
                  {/if}
                  {#if onrunner}
                    <button
                      class="msg-action-btn"
                      title="Run this on a schedule"
                      onclick={() => onrunner?.(promptOf(k))}
                      ><Icon name="calendar" size={13} />Make it an automation</button
                    >
                  {/if}
                {/if}
              {/if}
              {#if item.at}<span class="stamp" title={fullStamp(item.at)}>{clock(item.at)}</span>{/if}
            </div>
          {/if}
        </div>
      {:else if item.kind === 'thinking'}
        <details class="thinking-row">
          <summary>
            <Icon name="brain" size={13} />
            <span>Thinking</span>
          </summary>
          <div class="thinking-body">{item.text}</div>
        </details>
      {:else if item.kind === 'tool'}
        {#if item.name === SPAWN_TOOL}
          <SpawnCard tool={item} />
        {:else if item.name === PLAN_TOOL}
          <PlanCard tool={item} {onaction} answered={items.slice(i + 1).some((x) => x.kind === 'user')} />
        {:else if item.name === DOC_TOOL && onopendoc}
          <DocCard tool={item} onopen={onopendoc} />
        {:else if item.name === ASK_TOOL && !item.isError && item.status !== 'running'}
          <RequestCard
            request={requests[askedRequestId(item) ?? '']}
            fallbackTitle={String(item.input?.title ?? '')}
            {onanswer}
          />
        {:else if isDelegation(item.name)}
          <SubagentCard tool={item} />
        {:else}
          <ToolBlock tool={item} />
        {/if}
      {:else if item.kind === 'delivery'}
        <DeliveryCard
          runId={item.runId}
          title={item.title}
          excerpt={item.excerpt}
          schedule={item.schedule}
          at={item.at}
          onreply={onaction ? (text) => onaction?.(text, false) : undefined}
        />
      {:else if item.kind === 'request'}
        <RequestCard request={requests[item.requestId]} {onanswer} />
      {:else if item.kind === 'routed'}
        <div class="meta-row">Routed to <span class="meta-strong">{item.agent}</span></div>
      {:else if item.kind === 'footer'}
        <div class="meta-row" title={item.numTurns != null ? `${item.numTurns} model turns` : undefined}>
          {#if item.stopped}
            Stopped
          {:else}
            {footerLine(i, item.cost)}
          {/if}
        </div>
      {:else if item.kind === 'error'}
        <div class="card err-row" role="alert">
          <Icon name="alert-circle" size={16} />
          <span class="err-msg">{item.message}</span>
          {#if onretry && i === items.length - 1}
            <button class="btn-outline btn-sm" onclick={onretry}
              ><Icon name="refresh" size={13} />Retry</button
            >
          {/if}
        </div>
      {/if}
    {/if}
  </div>
{/each}

<style>
  /* Each turn eases in as it lands — one quiet fade, no bounce. */
  .chat-row {
    animation: row-in var(--dur-3) var(--transition) both;
  }
  @keyframes row-in {
    from {
      opacity: 0;
      transform: translateY(3px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }

  .day-divider {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 18px 0 10px;
    font-size: 11px;
    font-weight: 500;
    color: var(--muted);
  }
  .day-divider::before,
  .day-divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .err-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    color: var(--red);
    background: var(--red-soft);
    border-color: color-mix(in srgb, var(--red) 30%, transparent);
    font-size: 13px;
  }
  .err-row .err-msg {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
    color: var(--fg);
  }
  .err-row button {
    flex-shrink: 0;
  }

  .msg-user {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
  }
  .user-bubble {
    max-width: 80%;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 14px 14px 4px 14px;
    padding: 9px 14px;
    font-size: 15px;
    line-height: 1.5;
    color: var(--fg-bright);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  /* Assistant prose: borderless, generous. */
  .msg-assistant {
    padding: 2px 4px 2px 2px;
  }
  /* Hover/focus-revealed actions under a turn. */
  .row-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 2px;
    margin-top: 2px;
    min-height: 22px;
    opacity: 0;
    transition: opacity var(--dur-1) var(--transition);
  }
  .msg-assistant:hover .row-actions,
  .msg-assistant:focus-within .row-actions,
  .msg-assistant.rated .row-actions,
  .msg-user:hover .row-actions,
  .msg-user:focus-within .row-actions {
    opacity: 1;
  }
  @media (hover: none) {
    .row-actions {
      opacity: 1;
    }
  }
  .row-actions :global(.msg-action-btn.icon-only) {
    padding-left: 6px;
    padding-right: 6px;
  }
  .row-actions :global(.msg-action-btn.on) {
    color: var(--accent-soft);
    background: var(--accent-glow);
  }
  .stamp {
    font-size: 11px;
    color: var(--muted);
    padding: 0 6px;
    font-variant-numeric: tabular-nums;
  }

  /* Thinking: a quiet disclosure, not a card demanding attention. */
  .thinking-row {
    border-left: 2px solid var(--border2);
    border-radius: 0 6px 6px 0;
    transition: background var(--dur-1) var(--transition);
  }
  .thinking-row[open],
  .thinking-row:hover {
    background: var(--surface);
  }
  .thinking-row summary {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    cursor: pointer;
    list-style: none;
    user-select: none;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
  }
  .thinking-row summary::-webkit-details-marker {
    display: none;
  }
  .thinking-body {
    padding: 2px 14px 12px;
    font-size: 13px;
    line-height: 1.55;
    color: var(--fg-dim);
    white-space: pre-wrap;
  }

  .meta-row {
    padding: 4px 10px;
    font-size: 12px;
    color: var(--muted);
  }
  .meta-strong {
    color: var(--fg-dim);
    font-weight: 500;
  }
</style>
