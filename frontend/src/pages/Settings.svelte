<script lang="ts">
  // Settings: app-level configuration. "About me" — global instructions layered
  // into EVERY agent run's system prompt (chat, runners, spawns, manual runs),
  // bran's equivalent of Cowork's global instructions — plus Appearance (theme).
  import { api } from '../lib/api'
  import { errorText } from '../lib/errors'
  import { THEMES, getTheme, setTheme } from '../lib/theme'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'

  let instructions = $state('')
  let loaded = $state(false)
  let error = $state<string | null>(null) // load failure — banner with retry
  let saveError = $state('') // save failure — inline, so retry can't reload over typed text
  let savedFlash = $state(false)
  let saving = $state(false)
  let flashTimer: ReturnType<typeof setTimeout> | undefined

  const ABOUT_PLACEHOLDER =
    "e.g. I'm Ioan, a software engineer in London (Europe/London).\n" +
    'Keep briefings terse — bullets over prose. Currency in GBP.\n' +
    'I care about AI tooling, UK fintech, and the FTSE.'

  let theme = $state(getTheme())
  let tiles: HTMLButtonElement[] = []
  function chooseTheme(name: string) {
    theme = name
    setTheme(name)
  }
  // Radiogroup keyboard model: arrows move selection (and focus) through the
  // tiles; only the selected tile is tabbable.
  function themeKeydown(e: KeyboardEvent) {
    const dir =
      e.key === 'ArrowRight' || e.key === 'ArrowDown'
        ? 1
        : e.key === 'ArrowLeft' || e.key === 'ArrowUp'
          ? -1
          : 0
    if (!dir) return
    e.preventDefault()
    const idx = THEMES.findIndex((t) => t.name === theme)
    const next = (idx + dir + THEMES.length) % THEMES.length
    chooseTheme(THEMES[next].name)
    tiles[next]?.focus()
  }

  async function load() {
    error = null
    try {
      const s = await api.settings()
      instructions = s.user_instructions
    } catch (e) {
      error = String(e)
    } finally {
      loaded = true
    }
  }
  $effect(() => {
    void load()
  })

  async function save() {
    if (saving) return
    saving = true
    saveError = ''
    try {
      await api.saveSettings({ user_instructions: instructions })
      clearTimeout(flashTimer)
      savedFlash = true
      flashTimer = setTimeout(() => (savedFlash = false), 2200)
    } catch (e) {
      saveError = errorText(e)
    } finally {
      saving = false
    }
  }
</script>

<Page title="Settings">
  {#snippet subtitle()}how your agents work for you{/snippet}

  {#if error}<div style="margin-bottom: 14px;"><ErrorBanner {error} onretry={load} /></div>{/if}
  {#if loaded}
    <div class="space-y-4" style="max-width: 720px;">
      <div class="card">
        <div class="label-cap" style="margin-bottom: 6px;">About me · applied to every agent run</div>
        <p class="text-muted" style="font-size: 12.5px; margin: 0 0 10px; line-height: 1.5;">
          Who you are and how you like things done — role, location/timezone, preferences, standing context.
          Every agent sees this on every run: chats, runners, background spawns. Project instructions layer on
          top for project-specific guidance.
        </p>
        <textarea
          class="field"
          bind:value={instructions}
          rows="10"
          aria-label="about me — global instructions applied to every agent run"
          placeholder={ABOUT_PLACEHOLDER}
          onkeydown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              void save()
            }
          }}
          style="resize: vertical; font-family: var(--font-mono); font-size: 12.5px; line-height: 1.55;"
        ></textarea>
        <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px;">
          <button class="btn-primary" disabled={saving} onclick={save}>{saving ? 'saving…' : 'save'}</button>
          {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);"
              >saved ✓ — applies to the next run</span
            >{/if}
          {#if saveError}<span role="alert" style="color: var(--red); font-size: 12px;">{saveError}</span
            >{/if}
        </div>
      </div>

      <div class="card">
        <div class="label-cap" style="margin-bottom: 6px;">Appearance · this device</div>
        <p class="text-muted" style="font-size: 12.5px; margin: 0 0 12px; line-height: 1.5;">
          Applies instantly and is remembered by this browser.
        </p>
        <div class="theme-grid" role="radiogroup" aria-label="theme">
          {#each THEMES as t, i (t.name)}
            <button
              class="theme-tile"
              class:sel={theme === t.name}
              role="radio"
              aria-checked={theme === t.name}
              tabindex={theme === t.name ? 0 : -1}
              bind:this={tiles[i]}
              onclick={() => chooseTheme(t.name)}
              onkeydown={themeKeydown}
            >
              <span class="tile-preview" style="background: {t.bg};" aria-hidden="true">
                <span class="tile-card" style="background: {t.surface};">
                  <span class="tile-dot" style="background: {t.accent};"></span>
                  <span class="tile-line" style="background: {t.fg};"></span>
                  <span class="tile-line short" style="background: {t.fg};"></span>
                </span>
              </span>
              <span class="tile-name">{t.label}</span>
            </button>
          {/each}
        </div>
      </div>
    </div>
  {:else}
    <Skeleton rows={5} />
  {/if}
</Page>

<style>
  .theme-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
    gap: 10px;
  }
  .theme-tile {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 0;
    background: transparent;
    border: 0;
    text-align: left;
    cursor: pointer;
  }
  /* Each tile is a miniature of the theme itself — canvas, a raised card, an
     accent dot and two text lines. At this size Dracula and Nord actually
     look different, and no tooltip is needed: the name sits underneath. */
  .tile-preview {
    display: flex;
    align-items: flex-end;
    height: 58px;
    padding: 8px;
    border: 1px solid var(--border2);
    border-radius: var(--radius);
    transition:
      border-color var(--dur-1) var(--transition),
      box-shadow var(--dur-1) var(--transition);
  }
  .tile-card {
    display: flex;
    flex-direction: column;
    gap: 4px;
    width: 100%;
    padding: 6px 7px;
    border-radius: 4px;
  }
  .tile-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .tile-line {
    height: 3px;
    width: 80%;
    border-radius: 2px;
    opacity: 0.75;
  }
  .tile-line.short {
    width: 55%;
    opacity: 0.35;
  }
  .tile-name {
    font-size: 12px;
    color: var(--fg-dim);
    transition: color var(--dur-1) var(--transition);
  }
  .theme-tile:hover .tile-preview {
    border-color: var(--accent-soft);
  }
  .theme-tile:hover .tile-name {
    color: var(--fg);
  }
  .theme-tile.sel .tile-preview {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
  }
  .theme-tile.sel .tile-name {
    color: var(--accent-soft);
    font-weight: 500;
  }
  /* Keyboard focus gets the same ring the mouse never needs. */
  .theme-tile:focus-visible {
    outline: none;
  }
  .theme-tile:focus-visible .tile-preview {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
  }
</style>
