<script lang="ts">
  // Renders a list of ChatItems — the one render path for both live chat and
  // transcript replay. `streamingIndex` marks the assistant bubble that should
  // show a blinking cursor (live only).
  import type { ChatItem } from './events'
  import Prose from '../components/Prose.svelte'
  import ToolBlock from './ToolBlock.svelte'
  import SpawnCard from './SpawnCard.svelte'

  let { items, streamingIndex = -1 }: { items: ChatItem[]; streamingIndex?: number } = $props()
</script>

{#each items as item, i (i)}
  {#if item.kind === 'user'}
    <!-- Your turn: a compact right-aligned bubble (position says who it is). -->
    <div class="msg-user">
      <div class="user-bubble">{item.text}</div>
    </div>
  {:else if item.kind === 'assistant'}
    <!-- bran's turn: prose flows on the canvas, no card chrome — the answer is
         the page, not a boxed message. -->
    <div class="msg-assistant"><Prose text={item.text} streaming={i === streamingIndex} /></div>
  {:else if item.kind === 'thinking'}
    <details class="card" style="padding: 0;">
      <summary class="label-cap" style="padding: 10px 16px; cursor: pointer;">💭 thinking · {item.text.length} chars</summary>
      <div class="text-dim" style="padding: 4px 16px 12px; font-size: 12px; font-style: italic; white-space: pre-wrap;">{item.text}</div>
    </details>
  {:else if item.kind === 'tool'}
    {#if item.name === 'mcp__bran__spawn_agent'}
      <SpawnCard tool={item} />
    {:else}
      <ToolBlock tool={item} />
    {/if}
  {:else if item.kind === 'routed'}
    <div class="text-muted font-mono" style="padding: 4px 12px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em;">
      → routed to <span class="text-accent-soft">{item.agent}</span>
    </div>
  {:else if item.kind === 'footer'}
    <div class="text-muted font-mono" style="padding: 4px 12px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em;">
      done · {item.numTurns} turns{item.cost != null ? ` · $${item.cost.toFixed(4)}` : ''}
    </div>
  {:else if item.kind === 'error'}
    <div class="card" style="color: var(--red); border-color: color-mix(in srgb, var(--red) 30%, transparent);">{item.message}</div>
  {/if}
{/each}

<style>
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
  }
  /* Assistant prose: borderless, generous, with a touch of left inset so it
     reads as reading material rather than a chat box. */
  .msg-assistant {
    padding: 2px 4px 2px 2px;
    line-height: 1.65;
  }
</style>
