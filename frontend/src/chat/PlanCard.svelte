<script lang="ts">
  // Approval card for mcp__bran__propose_plan: the agent proposed a plan and
  // ended its turn; approving just sends a canned user message that resumes
  // the session. `onaction` is absent on read-only surfaces (run transcripts),
  // and `answered` hides the buttons once the user has already replied.
  import Prose from '../components/Prose.svelte'
  import Icon from '../components/Icon.svelte'
  import type { ToolItem } from './events'

  let {
    tool,
    answered = false,
    onaction,
  }: {
    tool: ToolItem
    answered?: boolean
    onaction?: (text: string, sendNow: boolean) => void
  } = $props()

  const title = $derived(((tool.input?.title as string) || 'Proposed plan').trim())
  const plan = $derived((tool.input?.plan as string) ?? '')
</script>

<div class="card plan-card">
  <header class="plan-head">
    <span class="plan-label"><Icon name="list" size={14} />Plan</span>
    <span class="plan-title">{title}</span>
    {#if !answered}<span class="pill accent ml-auto">Needs your approval</span>{/if}
  </header>
  <div class="plan-body"><Prose text={plan} /></div>
  {#if onaction && !answered}
    <footer class="plan-foot">
      <button class="btn-primary" onclick={() => onaction?.('Approved — go ahead with the plan.', true)}>
        <Icon name="check" size={14} />Approve
      </button>
      <button class="btn-outline" onclick={() => onaction?.('Revise the plan: ', false)}>
        <Icon name="pencil" size={14} />Revise
      </button>
    </footer>
  {:else if answered}
    <div class="plan-answered">Answered below</div>
  {/if}
</div>

<style>
  .plan-card {
    border-left: 2px solid var(--accent);
  }
  .plan-head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .plan-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
  }
  .plan-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .plan-body {
    font-size: 14px;
  }
  .plan-foot {
    display: flex;
    gap: 8px;
    margin-top: 12px;
  }
  .plan-answered {
    margin-top: 10px;
    font-size: 12px;
    color: var(--muted);
  }
</style>
