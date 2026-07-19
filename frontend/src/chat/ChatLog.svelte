<script lang="ts">
  // Renders a list of ChatItems — the one render path for both live chat and
  // transcript replay. `streamingIndex` marks the assistant bubble that should
  // show a blinking cursor (live only).
  import type { ChatItem, ToolItem } from './events'
  import Prose from '../components/Prose.svelte'
  import ToolBlock from './ToolBlock.svelte'
  import ToolGroup from './ToolGroup.svelte'
  import SpawnCard from './SpawnCard.svelte'
  import PlanCard from './PlanCard.svelte'
  import { toast } from '../lib/toast.svelte'

  let { items, streamingIndex = -1, onaction }: {
    items: ChatItem[]
    streamingIndex?: number
    // Lets interactive cards (PlanCard approve/revise) put a message in the
    // composer (and optionally send it). Absent on read-only surfaces.
    onaction?: (text: string, sendNow: boolean) => void
  } = $props()

  // Copy an assistant turn's markdown source; brief "copied" feedback inline.
  let copiedIdx = $state(-1)
  let copiedTimer: ReturnType<typeof setTimeout> | undefined
  async function copyText(text: string, i: number) {
    try {
      await navigator.clipboard.writeText(text)
      copiedIdx = i
      clearTimeout(copiedTimer)
      copiedTimer = setTimeout(() => (copiedIdx = -1), 2000)
    } catch {
      toast('copy failed', 'err')
    }
  }

  // Fold runs of 3+ consecutive same-name tool calls into one ToolGroup (keeps a
  // big fan-out scannable). Spawn/plan cards are never grouped — you want to see them.
  type Row =
    | { group: false; item: ChatItem; idx: number }
    | { group: true; name: string; tools: ToolItem[]; idx: number }
  const SPAWN = 'mcp__bran__spawn_agent'
  const PLAN = 'mcp__bran__propose_plan'
  const NEVER_GROUP = new Set([SPAWN, PLAN])
  const rows = $derived.by<Row[]>(() => {
    const out: Row[] = []
    let i = 0
    while (i < items.length) {
      const it = items[i]
      if (it.kind === 'tool' && !NEVER_GROUP.has(it.name)) {
        let j = i
        while (
          j < items.length && items[j].kind === 'tool' &&
          (items[j] as ToolItem).name === it.name && !NEVER_GROUP.has((items[j] as ToolItem).name)
        ) j++
        const run = items.slice(i, j) as ToolItem[]
        if (run.length >= 3) {
          out.push({ group: true, name: it.name, tools: run, idx: i })
        } else {
          run.forEach((t, k) => out.push({ group: false, item: t, idx: i + k }))
        }
        i = j
      } else {
        out.push({ group: false, item: it, idx: i })
        i++
      }
    }
    return out
  })

  // Stable per-row key so expansion state (a ToolBlock/ToolGroup the user opened
  // mid-stream) stays attached to its row as later events regroup the list —
  // index keys would let it bleed onto a different item. Tools carry a toolId;
  // text/thinking/footer rows key on position (they hold no local state).
  function rowKey(row: Row): string {
    if (row.group) return 'g:' + row.idx + ':' + (row.tools[0]?.toolId ?? '')
    if (row.item.kind === 'tool' && row.item.toolId) return 't:' + row.item.toolId
    return 'i:' + row.idx
  }
</script>

{#each rows as row (rowKey(row))}
  <div class="chat-row">
  {#if row.group}
    <ToolGroup name={row.name} tools={row.tools} />
  {:else}
    {@const item = row.item}
    {@const i = row.idx}
  {#if item.kind === 'user'}
    <!-- Your turn: a compact right-aligned bubble (position says who it is). -->
    <div class="msg-user">
      <div class="user-bubble">{item.text}</div>
    </div>
  {:else if item.kind === 'assistant'}
    <!-- bran's turn: prose flows on the canvas, no card chrome — the answer is
         the page, not a boxed message. Copy reveals on hover/focus. -->
    <div class="msg-assistant">
      <Prose text={item.text} streaming={i === streamingIndex} />
      {#if i !== streamingIndex}
        <div class="row-actions">
          <button class="msg-action-btn" class:done={copiedIdx === i}
                  onclick={() => copyText(item.text, i)}>
            {copiedIdx === i ? 'copied ✓' : 'copy'}
          </button>
        </div>
      {/if}
    </div>
  {:else if item.kind === 'thinking'}
    <details class="thinking-row">
      <summary class="label-cap">
        <svg class="think-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3a6 6 0 0 1 6 6c0 2.2-1.2 4.1-3 5.2V17a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1v-2.8c-1.8-1.1-3-3-3-5.2a6 6 0 0 1 6-6z"/><path d="M10 21h4"/></svg>
        thinking
      </summary>
      <div class="thinking-body text-dim">{item.text}</div>
    </details>
  {:else if item.kind === 'tool'}
    {#if item.name === 'mcp__bran__spawn_agent'}
      <SpawnCard tool={item} />
    {:else if item.name === 'mcp__bran__propose_plan'}
      <PlanCard tool={item} {onaction}
                answered={items.slice(i + 1).some((x) => x.kind === 'user')} />
    {:else}
      <ToolBlock tool={item} />
    {/if}
  {:else if item.kind === 'routed'}
    <div class="text-muted font-mono" style="padding: 4px 12px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em;">
      → routed to <span class="text-accent-soft">{item.agent}</span>
    </div>
  {:else if item.kind === 'footer'}
    <div class="text-muted font-mono" style="padding: 4px 12px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em;">
      {#if item.stopped}
        stopped
      {:else}
        done · {item.numTurns} turns{item.cost != null ? ` · $${item.cost.toFixed(4)}` : ''}
      {/if}
    </div>
  {:else if item.kind === 'error'}
    <div class="card" style="color: var(--red); border-color: color-mix(in srgb, var(--red) 30%, transparent);">{item.message}</div>
  {/if}
  {/if}
  </div>
{/each}

<style>
  /* Each turn eases in as it lands — one quiet fade, no bounce. History
     replay mounts everything at once, so the fade reads as a page settle. */
  .chat-row {
    animation: row-in var(--dur-3) var(--transition) both;
  }
  @keyframes row-in {
    from { opacity: 0; transform: translateY(3px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .msg-user {
    display: flex;
    justify-content: flex-end;
  }
  .user-bubble {
    max-width: 80%;
    background: var(--accent-glow);
    border: 1px solid color-mix(in srgb, var(--accent) 22%, transparent);
    border-radius: 16px 16px 5px 16px;
    padding: 9px 15px;
    font-family: var(--font-prose);
    font-size: 15px;
    line-height: 1.5;
    color: var(--fg-bright);
    white-space: pre-wrap;
    box-shadow: var(--shadow-sm);
  }
  /* Assistant prose: borderless, generous, with a touch of left inset so it
     reads as reading material rather than a chat box. */
  .msg-assistant {
    padding: 2px 4px 2px 2px;
    line-height: 1.65;
  }
  /* Hover/focus-revealed actions under an assistant turn. */
  .row-actions {
    display: flex;
    gap: 4px;
    margin-top: 2px;
    opacity: 0;
    transition: opacity var(--dur-1) var(--transition);
  }
  .msg-assistant:hover .row-actions,
  .msg-assistant:focus-within .row-actions { opacity: 1; }
  @media (hover: none) {
    .row-actions { opacity: 1; }
  }
  .msg-action-btn {
    background: transparent;
    border: 1px solid transparent;
    color: var(--muted);
    padding: 3px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    transition: color var(--dur-1), background var(--dur-1), border-color var(--dur-1);
  }
  .msg-action-btn:hover {
    color: var(--fg-dim);
    background: var(--surface2);
    border-color: var(--border2);
  }
  .msg-action-btn.done { color: var(--accent-soft); }

  /* Thinking: a quiet disclosure, not a card demanding attention. */
  .thinking-row {
    border-left: 2px solid var(--border2);
    border-radius: 0 var(--radius) var(--radius) 0;
    transition: background var(--dur-1) var(--transition);
  }
  .thinking-row[open],
  .thinking-row:hover { background: var(--surface); }
  .thinking-row summary {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    cursor: pointer;
    list-style: none;
    user-select: none;
  }
  .thinking-row summary::-webkit-details-marker { display: none; }
  .think-icon { color: var(--muted); flex-shrink: 0; }
  .thinking-body {
    padding: 2px 16px 12px;
    font-size: 12px;
    font-style: italic;
    white-space: pre-wrap;
  }
</style>
