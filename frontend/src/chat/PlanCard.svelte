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
    <span class="label-cap plan-label" style="color: var(--accent-soft);">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12h6M9 16h4"/></svg>
      plan · needs your approval
    </span>
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
  .plan-label {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .plan-body {
    font-size: 14px;
  }
</style>
