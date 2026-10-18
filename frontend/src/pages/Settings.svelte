<script lang="ts">
  // Settings: app-level configuration. "About me" — global instructions layered
  // into EVERY agent run's system prompt (chat, runners, spawns, manual runs) —
  // plus Appearance (theme).
  import { api } from '../lib/api'
  import { errorText } from '../lib/errors'
  import { THEMES, getTheme, setTheme } from '../lib/theme'
  import ErrorBanner from '../components/ErrorBanner.svelte'
  import Icon from '../components/Icon.svelte'
  import Page from '../components/Page.svelte'
  import Skeleton from '../components/Skeleton.svelte'
  import type { HealthSnapshot } from '../lib/types'

  let instructions = $state('')
  let health = $state<HealthSnapshot | null>(null)
  let healthFailed = $state(false)
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
    api
      .health()
      .then((h) => (health = h))
      .catch(() => (healthFailed = true))
  })

  const loginText = $derived.by(() => {
    if (!health) return ''
    const l = health.login
    const who = l.method === 'api_key' ? 'API key' : l.method === 'claude.ai' ? 'claude.ai' : 'none'
    const sub = l.subscription ? ` · ${l.subscription}` : ''
    return `${who}${sub} · ${l.ok ? 'ok' : 'expired'}`
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
  {#snippet subtitle()}How your agents work for you{/snippet}

  {#if error}<div style="margin-bottom: 14px;"><ErrorBanner {error} onretry={load} /></div>{/if}
  {#if loaded}
    <div class="space-y-4" style="max-width: 720px;">
      <section class="card">
        <h2 class="card-title">About me</h2>
        <p class="card-desc">
          Who you are and how you like things done: role, location and timezone, preferences, standing
          context. Every agent sees this on every run (chats, runners, background spawns). Project
          instructions layer on top for project-specific guidance.
        </p>
        <textarea
          class="field about"
          bind:value={instructions}
          rows="10"
          aria-label="About me: global instructions applied to every agent run"
          placeholder={ABOUT_PLACEHOLDER}
          onkeydown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
              e.preventDefault()
              void save()
            }
          }}></textarea>
        <div class="save-row">
          <button class="btn-primary" disabled={saving} onclick={save}>{saving ? 'Saving…' : 'Save'}</button>
          {#if savedFlash}<span class="saved" role="status"
              ><Icon name="check" size={14} />Saved. Applies to the next run.</span
            >{/if}
          {#if saveError}<span role="alert" class="save-err">{saveError}</span>{/if}
        </div>
      </section>

      <section class="card">
        <h2 class="card-title">Limits and integrations</h2>
        <p class="card-desc">What bounds every run, and what bran is connected to.</p>
        {#if health}
          <dl class="defs">
            <dt>Default model</dt>
            <dd>{health.limits.default_model}</dd>
            <dt>Per-run budget</dt>
            <dd>${health.limits.run_budget_usd}</dd>
            <dt>Run timeout</dt>
            <dd>{health.limits.run_timeout_s ? `${health.limits.run_timeout_s} s` : 'off'}</dd>
            <dt>Concurrent background runs</dt>
            <dd>{health.limits.max_concurrent_runs}</dd>
            <dt>Spawn depth</dt>
            <dd>{health.limits.max_spawn_depth}</dd>
            <dt>Subagent depth / concurrency</dt>
            <dd>{health.limits.subagent_depth} / {health.limits.subagent_concurrency}</dd>
            <dt>Runner retries</dt>
            <dd>{health.limits.runner_retries}</dd>
          </dl>
          <div class="defs-head">Integrations</div>
          <dl class="defs">
            <dt>Claude login</dt>
            <dd class:bad={!health.login.ok}>{loginText}</dd>
            <dt>Tavily</dt>
            <dd class:muted={!health.integrations.tavily}>
              {health.integrations.tavily ? 'Configured' : 'Not set'}
            </dd>
            <dt>Push notifications</dt>
            <dd class:muted={!health.integrations.webhook}>
              {health.integrations.webhook ? 'Configured' : 'Not set'}
            </dd>
          </dl>
          <div class="defs-head">Paths</div>
          <dl class="defs">
            <dt>Data folder</dt>
            <dd class="mono small">{health.paths.bran_home}</dd>
            <dt>Agents folder</dt>
            <dd class="mono small">{health.paths.agents_dir}</dd>
          </dl>
          <p class="foot">Set these in <code>.env</code> and restart <code>bran serve</code>.</p>
        {:else if healthFailed}
          <p class="card-desc" style="margin: 0;">Could not load the status snapshot.</p>
        {:else}
          <Skeleton rows={4} />
        {/if}
      </section>

      <section class="card">
        <h2 class="card-title">Appearance</h2>
        <p class="card-desc">Applies instantly and is remembered by this browser.</p>
        <div class="theme-grid" role="radiogroup" aria-label="Theme">
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
      </section>
    </div>
  {:else}
    <Skeleton rows={5} />
  {/if}
</Page>

<style>
  .card-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--fg-bright);
    margin: 0 0 4px;
  }
  .card-desc {
    font-size: 13px;
    color: var(--muted);
    line-height: 1.5;
    margin: 0 0 12px;
  }
  .about {
    resize: vertical;
    line-height: 1.55;
  }
  .save-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 10px;
  }
  .saved {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 12.5px;
    color: var(--green);
  }
  .save-err {
    color: var(--red);
    font-size: 12px;
  }
  .defs {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 6px 16px;
    margin: 0;
    font-size: 13px;
  }
  .defs dt {
    font-size: 12px;
    color: var(--muted);
    padding-top: 1px;
  }
  .defs dd {
    margin: 0;
    min-width: 0;
    color: var(--fg);
    overflow-wrap: anywhere;
  }
  .defs dd.bad {
    color: var(--red);
  }
  .defs dd.muted {
    color: var(--muted);
  }
  .defs dd.small {
    font-size: 12px;
  }
  .defs-head {
    font-size: 12px;
    font-weight: 500;
    color: var(--muted);
    margin: 14px 0 6px;
  }
  .foot {
    font-size: 12px;
    color: var(--muted);
    margin: 14px 0 0;
  }
  .foot code {
    font-family: var(--font-mono);
    font-size: 11.5px;
  }
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
     accent dot and two text lines. */
  .tile-preview {
    display: flex;
    align-items: flex-end;
    height: 58px;
    padding: 8px;
    border: 1px solid var(--border2);
    border-radius: 8px;
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
