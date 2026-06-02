<script lang="ts">
  // Renders a list of ChatItems — the one render path for both live chat and
  // transcript replay. `streamingIndex` marks the assistant bubble that should
  // show a blinking cursor (live only).
  import type { ChatItem } from './events'
  import Prose from '../components/Prose.svelte'
  import ToolBlock from './ToolBlock.svelte'

  let { items, streamingIndex = -1 }: { items: ChatItem[]; streamingIndex?: number } = $props()
</script>

{#each items as item, i (i)}
  {#if item.kind === 'user'}
    <div class="card" style="background: var(--accent-glow); border-color: rgba(255,255,255,0.04);">
      <div class="label-cap" style="color: var(--accent-soft); margin-bottom: 6px;">You</div>
      <div class="text-fg" style="font-family: var(--font-prose); font-size: 15px; line-height: 1.55; white-space: pre-wrap;">{item.text}</div>
    </div>
  {:else if item.kind === 'assistant'}
    <div class="card">
      <div class="label-cap" style="margin-bottom: 6px;">assistant</div>
      <div class="msg-prose"><Prose text={item.text} streaming={i === streamingIndex} /></div>
    </div>
  {:else if item.kind === 'thinking'}
    <details class="card" style="padding: 0;">
      <summary class="label-cap" style="padding: 10px 16px; cursor: pointer;">💭 thinking · {item.text.length} chars</summary>
      <div class="text-dim" style="padding: 4px 16px 12px; font-size: 12px; font-style: italic; white-space: pre-wrap;">{item.text}</div>
    </details>
  {:else if item.kind === 'tool'}
    <ToolBlock tool={item} />
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
