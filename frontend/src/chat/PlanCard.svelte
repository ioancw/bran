<script lang="ts">
  // Approval card for mcp__bran__propose_plan: the agent proposed a plan and
  // ended its turn; approving just sends a canned user message that resumes
  // the session. `onaction` is absent on read-only surfaces (run transcripts),
  // and `answered` hides the buttons once the user has already replied.
  import Prose from '../components/Prose.svelte'
  import type { ToolItem } from './events'

  let { tool, answered = false, onaction }: {
    tool: ToolItem
    answered?: boolean
    onaction?: (text: string, sendNow: boolean) => void
  } = $props()

  const title = $derived(((tool.input?.title as string) || 'Proposed plan').trim())
  const plan = $derived((tool.input?.plan as string) ?? '')
</script>

<div class="card plan-card">
  <header style="display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px;">
    <span class="label-cap" style="color: var(--accent-soft);">🗒 plan · needs your approval</span>
    <span class="text-bright" style="font-size: 14px; font-weight: 500;">{title}</span>
  </header>
  <div class="msg-prose plan-body"><Prose text={plan} /></div>
  {#if onaction && !answered}
    <footer style="display: flex; gap: 8px; margin-top: 12px;">
      <button class="btn-primary" onclick={() => onaction?.('Approved — go ahead with the plan.', true)}>approve →</button>
      <button class="btn-outline" onclick={() => onaction?.('Revise the plan: ', false)}>revise…</button>
    </footer>
  {:else if answered}
    <div class="label-cap" style="margin-top: 10px; color: var(--muted);">answered below</div>
  {/if}
</div>

<style>
  .plan-card {
    border-left: 2px solid var(--accent-soft);
  }
  .plan-body {
    font-size: 14px;
  }
</style>
