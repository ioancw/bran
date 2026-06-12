<script lang="ts">
  // Settings: app-level configuration. Currently just "About me" — global
  // instructions layered into EVERY agent run's system prompt (chat, runners,
  // spawns, manual runs), bran's equivalent of Cowork's global instructions.
  import { api } from '../lib/api'
  import { errorText } from '../lib/errors'
  import Page from '../components/Page.svelte'

  let instructions = $state('')
  let loaded = $state(false)
  let error = $state<string | null>(null)
  let savedFlash = $state(false)
  let saving = $state(false)

  $effect(() => {
    void (async () => {
      try {
        const s = await api.settings()
        instructions = s.user_instructions
      } catch (e) {
        error = String(e)
      } finally {
        loaded = true
      }
    })()
  })

  async function save() {
    if (saving) return
    saving = true
    error = null
    try {
      await api.saveSettings({ user_instructions: instructions })
      savedFlash = true
      setTimeout(() => (savedFlash = false), 2200)
    } catch (e) {
      error = String(e)
    } finally {
      saving = false
    }
  }
</script>

<Page title="Settings">
  {#snippet subtitle()}how your agents work for you{/snippet}

  {#if error}<div class="card" style="color: var(--red); margin-bottom: 14px;">{errorText(error)}</div>{/if}
  {#if loaded}
    <div class="card" style="max-width: 720px;">
      <div class="label-cap" style="margin-bottom: 6px;">About me · applied to every agent run</div>
      <p class="text-muted" style="font-size: 12.5px; margin: 0 0 10px; line-height: 1.5;">
        Who you are and how you like things done — role, location/timezone, preferences,
        standing context. Every agent sees this on every run: chats, runners, background
        spawns. Project instructions layer on top for project-specific guidance.
      </p>
      <textarea class="field" bind:value={instructions} rows="10"
                placeholder={'e.g. I\'m Ioan, a software engineer in London (Europe/London).\nKeep briefings terse — bullets over prose. Currency in GBP.\nI care about AI tooling, UK fintech, and the FTSE.'}
                style="resize: vertical; font-family: var(--font-mono); font-size: 12.5px; line-height: 1.55;"></textarea>
      <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px;">
        <button class="btn-primary" disabled={saving} onclick={save}>{saving ? 'saving…' : 'save'}</button>
        {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);">saved ✓ — applies to the next run</span>{/if}
      </div>
    </div>
  {:else}
    <div class="text-muted" style="padding: 24px; font-size: 13px; font-style: italic;">loading…</div>
  {/if}
</Page>
