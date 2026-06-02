<script lang="ts">
  // Live card for an orchestrator-spawned background run (mcp__bran__spawn_agent).
  // Pulls the run_id out of the tool result and polls it so the user sees the
  // fire-and-forget run progress inline, with a link to its detail page.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import StatusBadge from '../components/StatusBadge.svelte'
  import type { ToolItem } from './events'
  import type { RunRecord } from '../lib/types'

  let { tool }: { tool: ToolItem } = $props()

  const agentName = $derived((tool.input?.agent as string) ?? 'agent')
  const taskText = $derived((tool.input?.task as string) ?? '')
  const runId = $derived.by(() => {
    const m = tool.resultText?.match(/run_id=([0-9a-f-]{8,})/i)
    return m ? m[1] : null
  })

  let run = $state<RunRecord | null>(null)

  $effect(() => {
    const id = runId
    if (!id) return
    let timer: ReturnType<typeof setInterval> | undefined
    const poll = async () => {
      try {
        run = await api.run(id)
        if (run.status !== 'running' && run.status !== 'pending') clearInterval(timer)
      } catch {
        /* ignore transient errors */
      }
    }
    void poll()
    timer = setInterval(poll, 3000)
    return () => clearInterval(timer)
  })

  const status = $derived(run?.status ?? (runId ? 'running' : 'pending'))
</script>

<div class="card" style="border-color: color-mix(in srgb, var(--amber) 35%, var(--border));">
  <div style="display: flex; align-items: center; gap: 8px;">
    <span class="label-cap" style="color: var(--amber);">⤳ background run</span>
    <span class="mono text-accent-soft" style="font-size: 11px;">{agentName}</span>
    <span class="ml-auto"><StatusBadge {status} /></span>
  </div>
  {#if taskText}
    <div class="text-dim" style="font-size: 13px; margin-top: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{taskText}</div>
  {/if}
  {#if runId}
    <div style="margin-top: 6px;">
      <a href={href('/runs/' + runId)} use:link class="text-accent-soft" style="font-size: 11px; text-decoration: none;">view run {runId.slice(0, 8)} →</a>
    </div>
  {:else}
    <div class="text-muted" style="font-size: 11px; margin-top: 6px;">spawning…</div>
  {/if}
</div>
