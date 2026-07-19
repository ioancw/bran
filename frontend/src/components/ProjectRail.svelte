<script lang="ts">
  // The project-context right rail (Cowork-style), shared by the project hub
  // and the Chat page so context lives *beside* the conversation. Self-loads
  // the project's detail and owns its own edits (instructions, memory,
  // scheduled runners). Pass a projectId; it does the rest.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { workspace, loadProjects } from '../lib/workspace.svelte'
  import { relativeTime, fmtCost } from '../lib/time'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { toast } from '../lib/toast.svelte'
  import { errorText } from '../lib/errors'
  import Section from './Section.svelte'
  import Skeleton from './Skeleton.svelte'
  import StatusBadge from './StatusBadge.svelte'
  import CronField from './CronField.svelte'
  import type { AgentInfo, ProjectDetail, RunRecord } from '../lib/types'

  // `home` = the project page (configure: editable Knowledge + Scheduled open,
  // no live Progress). `chat` = beside a conversation (work: Progress on top,
  // config collapsed to reference).
  let { projectId, onrenamed, mode = 'home', sessionId = null }: {
    projectId: string
    onrenamed?: (name: string) => void
    mode?: 'home' | 'chat'
    // The chat this rail sits beside (chat mode). Scopes Progress to *this*
    // conversation's background runs rather than the whole project's.
    sessionId?: string | null
  } = $props()

  let data = $state<ProjectDetail | null>(null)
  let railLoading = $state(true)
  let agents = $state<AgentInfo[]>([])
  // Background runs spawned by the current chat (chat mode only).
  let chatRuns = $state<RunRecord[]>([])

  let name = $state('')
  let description = $state('')
  let instructions = $state('')
  let workDir = $state('')
  let savedFlash = $state(false)
  let saveError = $state('')
  let newMemory = $state('')

  let showSchedForm = $state(false)
  let sName = $state('')
  let sAgent = $state('orchestrator')
  let sCron = $state('0 8 * * *')
  let sTask = $state('')

  async function load() {
    try {
      data = await api.projectDetail(projectId)
      name = data.project.name
      description = data.project.description
      instructions = data.project.instructions
      workDir = data.project.work_dir ?? ''
    } catch {
      /* ignore — rail just stays empty */
    } finally {
      railLoading = false
    }
  }
  $effect(() => {
    void projectId
    void workspace.activityTick // reload Progress when a chat/run completes
    void load()
  })
  $effect(() => {
    void api.agents().then((a) => (agents = a)).catch(() => {})
  })
  // Chat-scoped Progress: this conversation's background runs only (reloads when
  // a turn finishes via activityTick). Empty for a brand-new, unsaved chat.
  $effect(() => {
    void workspace.activityTick
    const sid = sessionId
    if (mode !== 'chat' || !sid) {
      chatRuns = []
      return
    }
    void api.runs({ session_id: sid, exclude_chats: true, limit: 8 })
      .then((r) => (chatRuns = r))
      .catch(() => {})
  })

  async function save() {
    saveError = ''
    try {
      const saved = await api.saveProject(projectId, { name, description, instructions, work_dir: workDir })
      workDir = saved.work_dir // server echoes the resolved path
    } catch (e) {
      // Most commonly a 400 from working-folder validation (bad/missing path).
      saveError = e instanceof Error ? e.message : 'save failed'
      return
    }
    savedFlash = true
    onrenamed?.(name)
    void loadProjects() // keep names fresh elsewhere (sidebar, breadcrumb)
    setTimeout(() => (savedFlash = false), 2200)
  }
  async function addMem() {
    const t = newMemory.trim()
    if (!t) return
    try {
      await api.addMemory(projectId, t)
    } catch (e) {
      toast(errorText(e), 'err')
      return
    }
    newMemory = ''
    await load()
  }
  async function removeMem(id: string) {
    try {
      await api.deleteMemory(projectId, id)
    } catch (e) {
      toast(errorText(e), 'err')
      return
    }
    await load()
  }
  async function addSchedule() {
    if (!sName.trim() || !sCron.trim()) return
    try {
      await api.newSchedule({ name: sName.trim(), agent: sAgent, cron: sCron.trim(), task: sTask, project_id: projectId })
    } catch (e) {
      // Most often a bad cron or a duplicate runner name (400) — keep the form
      // open so the user can fix it rather than silently swallowing the error.
      toast(errorText(e), 'err')
      return
    }
    sName = ''
    sTask = ''
    showSchedForm = false
    await load()
  }
  async function removeSchedule(n: string) {
    if (!(await confirmDialog(`Delete runner "${n}"?`))) return
    try {
      await api.deleteSchedule(n)
    } catch (e) {
      toast(errorText(e), 'err')
      return
    }
    await load()
  }

  // --- Working files (managed-upload folder the agents read on demand) ---
  let fileInput = $state<HTMLInputElement | null>(null)
  let uploading = $state(false)
  let dragOver = $state(false)
  let fileError = $state('')

  async function doUpload(files: FileList | File[]) {
    if (!files.length) return
    uploading = true
    fileError = ''
    try {
      await api.uploadFiles(projectId, files)
      await load()
    } catch (e) {
      fileError = e instanceof Error ? e.message : 'upload failed'
    } finally {
      uploading = false
    }
  }
  function onPick(e: Event) {
    const input = e.currentTarget as HTMLInputElement
    if (input.files?.length) void doUpload(input.files)
    input.value = '' // allow re-picking the same file
  }
  function onDrop(e: DragEvent) {
    e.preventDefault()
    dragOver = false
    if (e.dataTransfer?.files?.length) void doUpload(e.dataTransfer.files)
  }
  // Direct URL to a project file (served inline: HTML docs render in the tab so
  // you can print to PDF; other types open/download per the browser).
  function fileUrl(name: string): string {
    return `/spa/projects/${encodeURIComponent(projectId)}/files/${encodeURIComponent(name)}`
  }
  async function removeFile(name: string) {
    if (!(await confirmDialog(`Remove "${name}" from this project?`))) return
    try {
      await api.deleteFile(projectId, name)
    } catch (e) {
      toast(errorText(e), 'err')
      return
    }
    await load()
  }
</script>

{#if !data && railLoading}
  <Skeleton rows={4} />
{:else if data}
  <div class="space-y-4">
    <!-- Progress is the chat-side "what's happening now"; the home page is for
         setup, so it leads with editable config instead. -->
    {#if mode === 'chat'}
      <Section label="Progress">
        <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">Background runs from this conversation.</p>
        {#if workspace.streaming}
          <div class="prog-live">
            <span class="live-dot sm"></span>
            <span class="text-bright" style="font-size: 12px;">{workspace.liveLabel || 'working…'}</span>
          </div>
        {/if}
        {#if chatRuns.length}
          <div class="space-y-2">
            {#each chatRuns.slice(0, 8) as r}
              <a href={href('/runs/' + r.id)} use:link class="prog-row" style="text-decoration: none;">
                <span class="text-bright" style="font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{r.agent}</span>
                <StatusBadge status={r.status} />
                <span class="text-muted" style="font-size: 10px; white-space: nowrap;">{relativeTime(r.started_at)}{#if r.total_cost_usd} · {fmtCost(r.total_cost_usd)}{/if}</span>
              </a>
            {/each}
          </div>
        {:else if !workspace.streaming}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">no activity yet</div>
        {/if}
      </Section>
    {/if}

    <!-- One "Knowledge" home for everything the agent knows: the always-on
         instructions (prose) and the discrete pinned facts. Expanded on the
         project page (configure), collapsed beside a chat (reference). -->
    <Section label="Knowledge" open={mode === 'home'}>
      {#if mode === 'home'}
        <!-- Project page: full editable config. -->
        <p class="text-muted" style="font-size: 11px; margin-bottom: 10px;">What the agent always knows here — applied to every chat & attached runner.</p>

        <div class="label-cap" style="margin-bottom: 6px;">Instructions · always on</div>
        <textarea class="field" bind:value={instructions} rows="7" aria-label="project instructions"
                  placeholder="How the agent should behave in this project…"
                  style="resize: vertical; font-family: var(--font-mono); font-size: 12px;"></textarea>
        <div style="display: flex; align-items: center; gap: 10px; margin-top: 8px;">
          <button class="btn-primary" onclick={save}>save</button>
          {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);">saved ✓</span>{/if}
        </div>

        <div class="rail-divider"></div>

        <div class="label-cap" style="margin-bottom: 6px;">Pinned facts</div>
        <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">Discrete facts — added here, via "remember that…", or /remember.</p>
        {#if data.memories.length}
          <div class="space-y-2" style="margin-bottom: 8px;">
            {#each data.memories as m}
              <div style="display: flex; gap: 8px; align-items: flex-start; font-size: 12px;">
                <span class="text-fg" style="flex: 1; line-height: 1.4;">{m.text}</span>
                <button class="btn-ghost" onclick={() => removeMem(m.id)} title="forget" aria-label="forget this fact">×</button>
              </div>
            {/each}
          </div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic; margin-bottom: 8px;">none yet</div>
        {/if}
        <div style="display: flex; gap: 6px;">
          <input class="field" bind:value={newMemory} placeholder="pin a fact…" aria-label="pin a fact"
                 onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addMem() } }} />
          <button class="btn-primary" onclick={addMem}>pin</button>
        </div>
      {:else}
        <!-- Beside a chat: read-only reference. Edit on the project page. -->
        <div class="label-cap" style="margin-bottom: 6px;">Instructions · always on</div>
        {#if instructions.trim()}
          <div class="ref-text">{instructions}</div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">none set</div>
        {/if}

        <div class="label-cap" style="margin: 12px 0 6px;">Pinned facts</div>
        {#if data.memories.length}
          <div class="space-y-1">
            {#each data.memories as m}
              <div class="text-fg" style="font-size: 12px; line-height: 1.4;">• {m.text}</div>
            {/each}
          </div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">none yet</div>
        {/if}

        <a href={href('/projects/' + encodeURIComponent(projectId))} use:link class="ref-edit">edit in project →</a>
      {/if}
    </Section>

    {#if mode === 'home'}
      <Section label="Scheduled" open={true}>
        {#snippet action()}
          <button class="btn-ghost" onclick={() => (showSchedForm = !showSchedForm)}>+ add</button>
        {/snippet}
        <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">Recurring runners for this project.</p>
        {#if showSchedForm}
          <div class="card-quiet" style="margin-bottom: 8px;">
            <input class="field" bind:value={sName} placeholder="name (unique)" aria-label="runner name" style="margin-bottom: 6px;" />
            <select class="field" bind:value={sAgent} aria-label="agent" style="margin-bottom: 6px;">
              {#each agents as a}<option value={a.name}>{a.name}</option>{/each}
            </select>
            <div style="margin-bottom: 6px;"><CronField bind:value={sCron} /></div>
            <textarea class="field" bind:value={sTask} rows="4" aria-label="runner task prompt"
              placeholder="prompt — what should the agent do each run?"
              style="resize: vertical; line-height: 1.5;"></textarea>
            <div style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px;">
              <button class="btn-ghost" onclick={() => (showSchedForm = false)}>cancel</button>
              <button class="btn-primary" onclick={addSchedule}>add</button>
            </div>
          </div>
        {/if}
        {#if data.schedules.length}
          <div class="space-y-2">
            {#each data.schedules as s}
              <div style="display: flex; gap: 8px; align-items: baseline; font-size: 12px;">
                <span class="text-bright">{s.name}</span>
                <span class="mono text-dim">{s.cron}</span>
                <button class="btn-ghost ml-auto" onclick={() => removeSchedule(s.name)} aria-label="delete runner {s.name}">×</button>
              </div>
            {/each}
          </div>
        {:else}
          <div class="text-muted" style="font-size: 12px; font-style: italic;">none yet</div>
        {/if}
      </Section>
    {:else if data.schedules.length}
      <!-- Beside a chat: read-only list of runners. -->
      <Section label="Scheduled" open={false}>
        <div class="space-y-2">
          {#each data.schedules as s}
            <div style="display: flex; gap: 8px; align-items: baseline; font-size: 12px;">
              <span class="text-bright">{s.name}</span>
              <span class="mono text-dim">{s.cron}</span>
            </div>
          {/each}
        </div>
      </Section>
    {/if}

    <Section label="Files" open={mode === 'home'}>
      <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">
        Uploaded files agents in this project can read on demand (PDFs, notes, data).
      </p>

      {#if data.files.length}
        <div class="space-y-2" style="margin-bottom: 10px;">
          {#each data.files as f}
            <div class="file-row">
              <a class="file-name mono" href={fileUrl(f.name)} target="_blank" rel="noopener"
                 title="open {f.name} in a new tab">{f.name}</a>
              <span class="text-dim" style="font-size: 10px; white-space: nowrap;">{f.size_human}</span>
              {#if mode === 'home'}
                <button class="btn-ghost" title="remove" onclick={() => removeFile(f.name)} aria-label="remove {f.name}">×</button>
              {/if}
            </div>
          {/each}
        </div>
      {:else}
        <div class="text-muted" style="font-size: 12px; font-style: italic; margin-bottom: 10px;">no files yet</div>
      {/if}

      {#if mode === 'home'}
        <!-- Working folder: a real directory agents may read AND write — the
             Cowork-style "do file work where I work". Distinct from the
             managed uploads above (read-only material the user provides). -->
        <div class="rail-divider"></div>
        <div class="label-cap" style="margin-bottom: 6px;">Working folder · read + write</div>
        <p class="text-muted" style="font-size: 11px; margin-bottom: 8px;">
          Agents create deliverables here (reports, CSVs, edited copies). Absolute path as the server sees it — WSL-style, e.g. <code>/mnt/c/Users/you/Documents/reports</code>.
        </p>
        <div style="display: flex; gap: 6px;">
          <input class="field mono" bind:value={workDir} placeholder="no working folder" aria-label="working folder path"
                 style="font-size: 11px;"
                 onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); save() } }} />
          <button class="btn-primary" onclick={save}>save</button>
        </div>
        {#if saveError}<div style="color: var(--red); font-size: 11px; margin-top: 6px;">{saveError}</div>{/if}
        {#if savedFlash}<div class="label-cap" style="color: var(--accent-soft); margin-top: 6px;">saved ✓</div>{/if}

        <div class="rail-divider"></div>
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
          class="dropzone"
          class:over={dragOver}
          role="button"
          tabindex="0"
          onclick={() => fileInput?.click()}
          onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && fileInput?.click()}
          ondragover={(e) => { e.preventDefault(); dragOver = true }}
          ondragleave={() => (dragOver = false)}
          ondrop={onDrop}
        >
          {#if uploading}uploading…{:else}<span class="text-dim">drop files here, or</span> <span class="text-bright">browse</span>{/if}
        </div>
        <input
          bind:this={fileInput}
          type="file"
          multiple
          style="display: none;"
          onchange={onPick}
        />
        {#if fileError}<div style="color: var(--red); font-size: 11px; margin-top: 6px;">{fileError}</div>{/if}
      {:else if workDir.trim()}
        <!-- Beside a chat: just show where the agent can produce files. -->
        <div class="rail-divider"></div>
        <div class="label-cap" style="margin-bottom: 4px;">Working folder · read + write</div>
        <div class="mono text-dim" style="font-size: 11px; word-break: break-all;">{workDir}</div>
      {/if}
    </Section>

    {#if mode === 'home'}
    <Section label="Settings" open={false}>
      <div class="label-cap" style="margin-bottom: 6px;">Name</div>
      <input class="field" bind:value={name} placeholder="name" aria-label="project name" style="margin-bottom: 10px;" />
      <div class="label-cap" style="margin-bottom: 6px;">Description</div>
      <input class="field" bind:value={description} placeholder="description" aria-label="project description" style="margin-bottom: 10px;" />
      <div style="display: flex; align-items: center; gap: 10px;">
        <button class="btn-primary" onclick={save}>save</button>
        {#if savedFlash}<span class="label-cap" style="color: var(--accent-soft);">saved ✓</span>{/if}
      </div>
    </Section>
    {/if}
  </div>
{/if}

<style>
  /* Working-files list + dropzone. */
  .file-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }
  .file-name {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--fg);
    text-decoration: none;
  }
  a.file-name:hover { color: var(--accent-soft); text-decoration: underline; }
  .dropzone {
    border: 1px dashed var(--border);
    border-radius: var(--radius);
    padding: 12px 10px;
    text-align: center;
    font-size: 11px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
  }
  .dropzone:hover { border-color: var(--accent-soft); }
  .dropzone.over {
    border-color: var(--accent);
    background: var(--accent-glow);
  }

  /* Read-only instructions preview in the chat rail. */
  .ref-text {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.5;
    color: var(--fg);
    white-space: pre-wrap;
    max-height: 180px;
    overflow-y: auto;
  }
  .ref-edit {
    display: inline-block;
    margin-top: 12px;
    font-size: 11px;
    color: var(--accent-soft);
    text-decoration: none;
  }
  .ref-edit:hover { text-decoration: underline; }
  .rail-divider {
    height: 1px;
    background: var(--border);
    margin: 14px 0;
  }
  .prog-live {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    margin-bottom: 6px;
    border-radius: var(--radius);
    background: var(--accent-glow);
  }
  .prog-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    border-radius: var(--radius);
    transition: background 0.12s var(--transition);
  }
  .prog-row:hover { background: var(--surface2); }
</style>
