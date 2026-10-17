<script lang="ts">
  // Friendly schedule input: type natural language ("every weekday at 8am") OR a
  // raw cron, get a live "Runs every weekday at 08:00" preview (or a parse
  // error), plus quick-pick presets. Backed by the same nl_cron parser the chat
  // uses, so the form and the conversation interpret schedules identically.
  // Bind `value`; it holds whatever the user typed — the backend normalises it
  // to cron on save.
  import { api } from '../lib/api'

  let {
    value = $bindable(''),
    placeholder = 'e.g. every weekday at 8am',
  }: {
    value?: string
    placeholder?: string
  } = $props()

  let human = $state('')
  let error = $state('')
  const uid = $props.id()

  const PRESETS = [
    { label: 'Weekdays 8am', expr: 'every weekday at 8am' },
    { label: 'Daily 8am', expr: 'daily at 8am' },
    { label: 'Hourly', expr: 'every hour' },
    { label: 'Mondays 9am', expr: 'every Monday at 9am' },
  ]

  async function check(expr: string) {
    const v = expr.trim()
    if (!v) {
      human = ''
      error = ''
      return
    }
    try {
      const r = await api.parseSchedule(v)
      if (r.ok) {
        human = r.human
        error = ''
      } else {
        human = ''
        error = r.error
      }
    } catch {
      human = ''
      error = ''
    }
  }

  // Debounced live preview as the user types.
  $effect(() => {
    const v = value
    const t = setTimeout(() => void check(v), 300)
    return () => clearTimeout(t)
  })

  // The server's phrase is lowercase ("every weekday at 08:00"); lead with a verb.
  const preview = $derived(human ? `Runs ${human.charAt(0).toLowerCase()}${human.slice(1)}` : '')
</script>

<div>
  <input
    class="field"
    bind:value
    {placeholder}
    aria-label="Schedule"
    aria-describedby="{uid}-meta"
    aria-invalid={error ? true : undefined}
  />
  <!-- role="status": the parse result / error is announced as it changes,
       not just painted under the input. -->
  <div class="cron-meta" id="{uid}-meta" role="status">
    {#if error}
      <span class="cron-err">{error.split('\n')[0]}</span>
    {:else if preview}
      <span class="cron-ok">{preview}</span>
    {:else}
      <span class="cron-hint">Plain English or cron. Runs in the server's timezone.</span>
    {/if}
  </div>
  <div class="cron-presets">
    {#each PRESETS as p (p.expr)}
      <button type="button" class="btn-outline btn-sm" onclick={() => (value = p.expr)}>{p.label}</button>
    {/each}
  </div>
</div>

<style>
  .cron-meta {
    min-height: 16px;
    margin-top: 5px;
    font-size: 12px;
    line-height: 1.3;
  }
  .cron-ok {
    color: var(--fg-dim);
  }
  .cron-err {
    color: var(--red);
  }
  .cron-hint {
    color: var(--muted);
  }
  .cron-presets {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
  }
</style>
