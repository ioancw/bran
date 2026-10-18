<script lang="ts">
  // A run as a list of steps: what happened, in order, with the clock time
  // and how long each tool call took. Rows expand to the full detail (the
  // same ToolBlock / prose the transcript uses), so the timeline is the
  // overview and the transcript the long form.
  import { toolDisplayName, type ChatItem, type ToolItem } from './events'
  import { category, categoryIcon, preview, summary, truncate } from './tool-labels'
  import ToolBlock from './ToolBlock.svelte'
  import Prose from '../components/Prose.svelte'
  import Icon from '../components/Icon.svelte'

  let { items }: { items: ChatItem[] } = $props()

  let openRows = $state<Record<number, boolean>>({})
  function toggle(i: number) {
    openRows[i] = !openRows[i]
  }

  function clockSeconds(iso: string | undefined): string {
    if (!iso) return ''
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }
  function fmtMs(ms: number | null | undefined): string {
    if (ms == null) return ''
    return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
  }
  function firstLine(text: string, max = 110): string {
    const line = text.split('\n').find((l) => l.trim()) ?? ''
    return truncate(line.trim(), max)
  }
  function isDelegation(t: ToolItem): boolean {
    const n = t.name.toLowerCase()
    return n === 'agent' || n === 'task'
  }

  type Row = {
    i: number
    item: ChatItem
    time: string
    icon: string
    label: string
    detail: string
    right: string
    tone: '' | 'ok' | 'err' | 'warn'
    expandable: boolean
  }
  const rows = $derived.by((): Row[] => {
    const out: Row[] = []
    items.forEach((item, i) => {
      if (item.kind === 'routed') return
      if (item.kind === 'user') {
        out.push({
          i,
          item,
          time: clockSeconds(item.at),
          icon: 'message-square',
          label: 'Prompt',
          detail: firstLine(item.text),
          right: '',
          tone: '',
          expandable: true,
        })
      } else if (item.kind === 'assistant') {
        out.push({
          i,
          item,
          time: clockSeconds(item.at),
          icon: 'bot',
          label: 'Reply',
          detail: firstLine(item.text),
          right: '',
          tone: '',
          expandable: true,
        })
      } else if (item.kind === 'thinking') {
        out.push({
          i,
          item,
          time: clockSeconds(item.at),
          icon: 'lightbulb',
          label: 'Thinking',
          detail: firstLine(item.text),
          right: '',
          tone: '',
          expandable: !!item.text.trim(),
        })
      } else if (item.kind === 'tool') {
        const cat = category(item.name)
        const label = isDelegation(item)
          ? `Delegated to ${(item.input.subagent_type as string) || 'general-purpose'}`
          : toolDisplayName(item.name)
        const detail = isDelegation(item)
          ? truncate(String(item.input.prompt ?? item.input.description ?? ''), 110)
          : summary(item.name, item.input)
        const tone = item.status === 'error' ? 'err' : item.status === 'running' ? 'warn' : 'ok'
        const right =
          item.status === 'running'
            ? 'working…'
            : [preview(item.name, item.resultText, item.isError), fmtMs(item.durationMs)]
                .filter(Boolean)
                .join(' · ')
        out.push({
          i,
          item,
          time: clockSeconds(item.at),
          icon: categoryIcon(cat),
          label,
          detail,
          right,
          tone,
          expandable: true,
        })
      } else if (item.kind === 'footer') {
        const detail = item.stopped
          ? 'Stopped'
          : [
              item.numTurns != null ? `${item.numTurns} turns` : '',
              item.cost != null ? `$${item.cost.toFixed(4)}` : '',
            ]
              .filter(Boolean)
              .join(' · ')
        out.push({
          i,
          item,
          time: '',
          icon: 'circle-check',
          label: 'Done',
          detail,
          right: '',
          tone: 'ok',
          expandable: false,
        })
      } else if (item.kind === 'error') {
        out.push({
          i,
          item,
          time: '',
          icon: 'alert-circle',
          label: 'Error',
          detail: firstLine(item.message),
          right: '',
          tone: 'err',
          expandable: true,
        })
      }
    })
    return out
  })
</script>

<ol class="timeline">
  {#each rows as r (r.i)}
    <li class="step" class:open={openRows[r.i]}>
      <button
        type="button"
        class="step-row"
        class:expandable={r.expandable}
        disabled={!r.expandable}
        aria-expanded={r.expandable ? !!openRows[r.i] : undefined}
        onclick={() => r.expandable && toggle(r.i)}
      >
        <span class="step-time mono">{r.time}</span>
        <span class="step-mark tone-{r.tone}"><Icon name={r.icon} size={13} /></span>
        <span class="step-label">{r.label}</span>
        <span class="step-detail" title={r.detail}>{r.detail}</span>
        <span class="step-right tone-{r.tone}">{r.right}</span>
      </button>
      {#if openRows[r.i]}
        <div class="step-body">
          {#if r.item.kind === 'tool'}
            <ToolBlock tool={r.item} open />
          {:else if r.item.kind === 'assistant'}
            <div class="step-prose"><Prose text={r.item.text} /></div>
          {:else if r.item.kind === 'user' || r.item.kind === 'thinking'}
            <div class="step-text">{r.item.text}</div>
          {:else if r.item.kind === 'error'}
            <div class="step-text tone-err">{r.item.message}</div>
          {/if}
        </div>
      {/if}
    </li>
  {/each}
</ol>

<style>
  .timeline {
    list-style: none;
    margin: 0;
    padding: 0;
    position: relative;
  }
  /* The spine: one hairline down the marks column. */
  .timeline::before {
    content: '';
    position: absolute;
    left: calc(76px + 11px);
    top: 10px;
    bottom: 10px;
    width: 1px;
    background: var(--border);
  }
  .step-row {
    display: grid;
    grid-template-columns: 76px 22px minmax(90px, max-content) minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 5px 6px 5px 0;
    background: none;
    border: 0;
    border-radius: 6px;
    text-align: left;
    color: inherit;
    font: inherit;
    cursor: default;
  }
  .step-row.expandable {
    cursor: pointer;
  }
  .step-row.expandable:hover {
    background: var(--surface2);
  }
  .step-time {
    font-size: 11px;
    color: var(--muted);
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .step-mark {
    position: relative;
    z-index: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: var(--surface);
    border: 1px solid var(--border2);
    color: var(--muted);
  }
  .step-mark.tone-ok {
    color: var(--green);
    border-color: color-mix(in srgb, var(--green) 40%, var(--border2));
  }
  .step-mark.tone-err {
    color: var(--red);
    border-color: color-mix(in srgb, var(--red) 50%, var(--border2));
  }
  .step-mark.tone-warn {
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 50%, var(--border2));
    animation: brn-pulse 1.4s ease-in-out infinite;
  }
  .step-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
    white-space: nowrap;
  }
  .step-detail {
    font-size: 12.5px;
    color: var(--fg-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .step-right {
    font-size: 11.5px;
    color: var(--muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .step-right.tone-err {
    color: var(--red);
  }
  .step-body {
    margin: 2px 0 8px calc(76px + 32px);
  }
  .step-text {
    font-size: 13px;
    color: var(--fg-dim);
    white-space: pre-wrap;
    line-height: 1.55;
    padding: 6px 10px;
    background: var(--surface2);
    border-radius: 8px;
  }
  .step-text.tone-err {
    color: var(--red);
  }
  .step-prose {
    padding: 4px 2px;
  }
  @media (max-width: 768px) {
    .step-row {
      grid-template-columns: 22px minmax(0, 1fr) auto;
    }
    .step-time {
      display: none;
    }
    .step-label {
      grid-column: 2;
    }
    .step-detail {
      grid-column: 2;
      grid-row: 2;
    }
    .step-right {
      grid-column: 3;
      grid-row: 1 / span 2;
    }
    .timeline::before {
      left: 11px;
    }
    .step-body {
      margin-left: 32px;
    }
  }
</style>
