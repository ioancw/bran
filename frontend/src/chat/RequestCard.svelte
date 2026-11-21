<script lang="ts">
  // Something the teammate asked before acting. Nothing has changed while it
  // waits; approving makes the change described (bran does it, not the
  // agent), declining leaves everything as it is. Either way the teammate is
  // told the answer, and the note, on its next run or message.
  import Icon from '../components/Icon.svelte'
  import { localDateTime } from '../lib/time'
  import type { TeammateRequest } from '../lib/types'

  let {
    request,
    fallbackTitle = '',
    onanswer,
  }: {
    // Undefined while it is still being looked up (just asked, or loading).
    request: TeammateRequest | undefined
    fallbackTitle?: string
    // Absent on read-only surfaces.
    onanswer?: (id: string, decision: 'approve' | 'decline', note: string) => Promise<void>
  } = $props()

  // Names arrive in code quotes; here they are plain words.
  const plain = (s: string) => s.replaceAll('`', '')

  let note = $state('')
  let busy = $state<'approve' | 'decline' | null>(null)

  async function answer(decision: 'approve' | 'decline') {
    if (!request || !onanswer || busy) return
    busy = decision
    try {
      await onanswer(request.id, decision, note.trim())
    } finally {
      busy = null
    }
  }

  // The words a yes puts into effect, when there are any to read first.
  const wording = $derived(
    request?.action.kind === 'new_automation'
      ? { label: 'What each run would do', text: request.action.task ?? '' }
      : request?.action.kind === 'change_brief'
        ? { label: 'The new brief', text: request.action.brief ?? '' }
        : null,
  )
</script>

<article class="card ask" class:open={request?.status === 'pending'}>
  <header class="ask-head">
    <span class="ask-label"><Icon name="help-circle" size={14} />Asking first</span>
    {#if request?.status === 'pending'}
      <span class="pill accent ml-auto">Waiting for your answer</span>
    {:else if request?.status === 'approved'}
      <span class="pill ok ml-auto"><Icon name="check" size={12} />Approved</span>
    {:else if request?.status === 'declined'}
      <span class="pill mute ml-auto">Declined</span>
    {/if}
  </header>
  <h4 class="ask-title">{request?.title ?? fallbackTitle}</h4>
  {#if request?.detail}<p class="ask-detail">{request.detail}</p>{/if}

  {#if request}
    {#if request.status === 'pending'}
      <p class="ask-does">
        {request.summary
          ? `If you approve: ${plain(request.summary)}`
          : 'If you approve, it does this from its next run on.'}
        Nothing has changed yet.
      </p>
      {#if wording?.text}
        <details class="ask-wording">
          <summary>{wording.label}</summary>
          <p>{wording.text}</p>
        </details>
      {/if}
      {#if onanswer}
        <div class="ask-answer">
          <input
            class="field ask-note"
            bind:value={note}
            maxlength="1000"
            placeholder="Add a note for it (optional)"
            aria-label="A note to go with your answer"
            onkeydown={(e) => {
              if (e.key === 'Enter') void answer('approve')
            }}
          />
          <button class="btn-primary btn-sm" disabled={!!busy} onclick={() => answer('approve')}>
            <Icon name="check" size={13} />{busy === 'approve' ? 'Approving…' : 'Approve'}
          </button>
          <button class="btn-outline btn-sm" disabled={!!busy} onclick={() => answer('decline')}>
            {busy === 'decline' ? 'Declining…' : 'Decline'}
          </button>
        </div>
      {/if}
    {:else}
      <p class="ask-does">
        {#if request.status === 'approved'}
          {request.outcome ? `Done: bran ${plain(request.outcome)}.` : 'It does this from its next run on.'}
        {:else}
          Nothing was changed.
        {/if}
        {#if request.decided_at}<span class="ask-when">{localDateTime(request.decided_at)}</span>{/if}
      </p>
      {#if request.note}<p class="ask-said">You said: {request.note}</p>{/if}
    {/if}
  {/if}
</article>

<style>
  .ask {
    max-width: 640px;
  }
  .ask.open {
    border-left: 2px solid var(--accent);
  }
  .ask-head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 6px;
    flex-wrap: wrap;
  }
  .ask-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
  }
  .ask-title {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--fg-bright);
  }
  .ask-detail {
    margin: 4px 0 0;
    font-size: 14px;
    white-space: pre-wrap;
  }
  .ask-does,
  .ask-said {
    margin: 8px 0 0;
    font-size: 13px;
    color: var(--fg-dim);
  }
  .ask-when {
    color: var(--muted);
    margin-left: 6px;
  }
  .ask-wording {
    margin-top: 8px;
    font-size: 13px;
  }
  .ask-wording summary {
    cursor: pointer;
    color: var(--fg-dim);
  }
  .ask-wording p {
    margin: 6px 0 0;
    padding: 8px 10px;
    background: var(--surface2);
    border-radius: 6px;
    white-space: pre-wrap;
  }
  .ask-answer {
    display: flex;
    gap: 8px;
    margin-top: 12px;
    flex-wrap: wrap;
  }
  .ask-note {
    flex: 1 1 220px;
    min-width: 0;
  }
</style>
