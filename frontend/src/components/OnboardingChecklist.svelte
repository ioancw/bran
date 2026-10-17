<script lang="ts">
  // "Get to know bran" — a small progress checklist in the left rail, modelled
  // on Cowork's onboarding widget. Steps derive from real state (does a project
  // exist, has a chat happened, is a runner scheduled). Dismissible; hides once
  // every step is done.
  import { href, link } from '../lib/router.svelte'
  import Icon from './Icon.svelte'

  let {
    projectDone,
    chatDone,
    runnerDone,
  }: {
    projectDone: boolean
    chatDone: boolean
    runnerDone: boolean
  } = $props()

  const DISMISS_KEY = 'bran.onboarding.dismissed'
  let dismissed = $state(typeof localStorage !== 'undefined' && localStorage.getItem(DISMISS_KEY) === '1')
  function dismiss() {
    dismissed = true
    try {
      localStorage.setItem(DISMISS_KEY, '1')
    } catch {
      /* ignore */
    }
  }

  const steps = $derived([
    { done: projectDone, label: 'Create a project', to: '/projects' },
    { done: chatDone, label: 'Start a chat', to: '/chat' },
    { done: runnerDone, label: 'Schedule a runner', to: '/runners' },
  ])
  const doneCount = $derived(steps.filter((s) => s.done).length)
  const allDone = $derived(doneCount === steps.length)
</script>

{#if !dismissed && !allDone}
  <div class="onboard">
    <div class="onboard-head">
      <span class="label-cap">Get to know bran</span>
      <span class="ml-auto label-cap">{doneCount}/{steps.length}</span>
      <button class="btn-icon onboard-x" onclick={dismiss} title="Dismiss" aria-label="Dismiss"><Icon name="x" size={12} /></button>
    </div>
    <div class="onboard-bar">
      <div class="onboard-fill" style="width: {(doneCount / steps.length) * 100}%;"></div>
    </div>
    <div class="onboard-steps">
      {#each steps as s (s.label)}
        <a href={href(s.to)} use:link class="onboard-step" class:done={s.done}>
          <span class="onboard-check" class:done={s.done}>{#if s.done}<Icon name="check" size={9} stroke={3} />{/if}</span>
          <span style="text-decoration: {s.done ? 'line-through' : 'none'};">{s.label}</span>
        </a>
      {/each}
    </div>
  </div>
{/if}

<style>
  .onboard {
    border-radius: var(--radius-md);
    padding: 10px 12px;
    background: var(--surface2);
  }
  .onboard-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .onboard-x {
    width: 20px;
    height: 20px;
  }
  .onboard-bar {
    height: 3px;
    border-radius: 3px;
    background: var(--border2);
    margin: 8px 0 10px;
    overflow: hidden;
  }
  .onboard-fill {
    height: 100%;
    background: var(--accent-soft);
    transition: width 0.3s var(--transition);
  }
  .onboard-steps {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .onboard-step {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--fg-dim);
    text-decoration: none;
    padding: 2px 0;
  }
  .onboard-step:hover {
    color: var(--fg-bright);
  }
  .onboard-step.done {
    color: var(--muted);
  }
  .onboard-check {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 1px solid var(--border2);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 9px;
    flex-shrink: 0;
  }
  .onboard-check.done {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--accent-fg);
  }
</style>
