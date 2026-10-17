<script lang="ts">
  // Renders a list of ChatItems — the one render path for both live chat and
  // transcript replay. `streamingIndex` marks the assistant bubble that should
  // show a blinking cursor (live only).
  import { groupRows, type ChatItem, type Row, SPAWN_TOOL, PLAN_TOOL } from './events'
  import { clock, fullStamp, dayKey, dayLabel } from './format'
  import Prose from '../components/Prose.svelte'
  import Icon from '../components/Icon.svelte'
  import ToolBlock from './ToolBlock.svelte'
  import ToolGroup from './ToolGroup.svelte'
  import SpawnCard from './SpawnCard.svelte'
  import PlanCard from './PlanCard.svelte'
  import { toast } from '../lib/toast.svelte'

  let {
    items,
    streamingIndex = -1,
    onaction,
    onretry,
  }: {
    items: ChatItem[]
    streamingIndex?: number
    // Lets interactive cards (PlanCard approve/revise) and the user-message
    // "Edit" action put a message in the composer (and optionally send it).
    // Absent on read-only surfaces.
    onaction?: (text: string, sendNow: boolean) => void
    // Re-send the turn that produced a trailing error row. Only the live chat
    // passes this; transcript replays render errors as plain records.
    onretry?: () => void
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

  // Stable per-row key so expansion state (a ToolBlock/ToolGroup the user opened
  // mid-stream) stays attached to its row as later events regroup the list —
  // index keys would let it bleed onto a different item. Tools carry a toolId;
  // text/thinking/footer rows key on position (they hold no local state).
  function rowKey(row: Row): string {
    if (row.group) return 'g:' + (row.tools[0]?.toolId ?? row.idx)
    if (row.item.kind === 'tool' && row.item.toolId) return 't:' + row.item.toolId
    return 'i:' + row.idx
  }

  // Day dividers: emitted before a row whose calendar day differs from the
  // last timestamped row before it. Rows without a timestamp never divide.
  function stampOf(row: Row): string | undefined {
    if (row.group) return undefined
    const it = row.item
    return it.kind === 'user' || it.kind === 'assistant' ? it.at : undefined
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
</script>

{#each rows as row, ri (rowKey(row))}
  {#if dividers[ri]}
    <div class="day-divider" role="separator">
      <span>{dayLabel(stampOf(row)!)}</span>
    </div>
  {/if}
  <div class="chat-row">
    {#if row.group}
      <ToolGroup tools={row.tools} />
    {:else}
      {@const item = row.item}
      {@const i = row.idx}
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
      {:else if item.kind === 'assistant'}
        <!-- bran's turn: prose flows on the canvas, no card chrome — the answer is
         the page, not a boxed message. Actions reveal on hover/focus. -->
        <div class="msg-assistant">
          <Prose text={item.text} streaming={i === streamingIndex} class="reading" />
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
        {:else}
          <ToolBlock tool={item} />
        {/if}
      {:else if item.kind === 'routed'}
        <div class="meta-row">Routed to <span class="meta-strong">{item.agent}</span></div>
      {:else if item.kind === 'footer'}
        <div class="meta-row">
          {#if item.stopped}
            Stopped
          {:else}
            Done · {item.numTurns ?? '—'} turns{item.cost != null ? ` · $${item.cost.toFixed(4)}` : ''}
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
    gap: 2px;
    margin-top: 2px;
    min-height: 22px;
    opacity: 0;
    transition: opacity var(--dur-1) var(--transition);
  }
  .msg-assistant:hover .row-actions,
  .msg-assistant:focus-within .row-actions,
  .msg-user:hover .row-actions,
  .msg-user:focus-within .row-actions {
    opacity: 1;
  }
  @media (hover: none) {
    .row-actions {
      opacity: 1;
    }
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
