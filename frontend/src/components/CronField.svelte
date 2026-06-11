<script lang="ts">
  // Friendly schedule input: type natural language ("every weekday at 8am") OR a
  // raw cron, get a live "→ every weekday at 08:00" preview (or a parse error),
  // plus quick-pick presets. Backed by the same nl_cron parser the chat uses, so
  // the form and the conversation interpret schedules identically. Bind `value`;
  // it holds whatever the user typed — the backend normalises it to cron on save.
  import { api } from '../lib/api'

  let { value = $bindable(''), placeholder = 'e.g. every weekday at 8am' }: {
    value?: string
    placeholder?: string
  } = $props()

  let human = $state('')
  let error = $state('')

  const PRESETS = [
    { label: 'Weekdays 8am', expr: 'every weekday at 8am' },
    { label: 'Daily 8am', expr: 'daily at 8am' },
    { label: 'Hourly', expr: 'every hour' },
    { label: 'Mon 9am', expr: 'every Monday at 9am' },
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
</script>

<div>
  <input class="field" bind:value {placeholder} />
  <div class="cron-meta">
    {#if error}
      <span class="cron-err">{error.split('\n')[0]}</span>
    {:else if human}
      <span class="cron-ok">→ {human}</span>
    {:else}
      <span class="cron-hint">plain English or cron · runs in the server's timezone</span>
    {/if}
  </div>
  <div class="cron-presets">
    {#each PRESETS as p}
      <button type="button" class="cron-chip" onclick={() => (value = p.expr)}>{p.label}</button>
    {/each}
  </div>
</div>

<style>
  .cron-meta { min-height: 15px; margin-top: 4px; font-size: 11px; line-height: 1.3; }
  .cron-ok { color: var(--accent-soft); }
  .cron-err { color: var(--red); }
  .cron-hint { color: var(--muted); font-style: italic; }
  .cron-presets { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .cron-chip {
    background: transparent;
    border: 1px solid var(--border2);
    border-radius: 999px;
    padding: 2px 9px;
    font-size: 11px;
    color: var(--fg-dim);
    cursor: pointer;
    transition: all 0.12s var(--transition);
  }
  .cron-chip:hover { color: var(--fg-bright); border-color: var(--muted); }
</style>
