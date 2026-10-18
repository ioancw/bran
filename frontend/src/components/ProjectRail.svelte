<script lang="ts">
  // The project-context right rail (Cowork-style), shared by the project hub
  // and the Chat page so context lives *beside* the conversation. Self-loads
  // the project's detail and owns its own edits (instructions, memory,
  // scheduled runners). Pass a projectId; it does the rest.
  import { api } from '../lib/api'
  import { href, link } from '../lib/router.svelte'
  import { workspace, loadProjects, bumpActivity } from '../lib/workspace.svelte'
  import { relativeTime, fmtCost } from '../lib/time'
  import { confirmDialog } from '../lib/confirm.svelte'
  import { toast } from '../lib/toast.svelte'
  import { errorText } from '../lib/errors'
  import Section from './Section.svelte'
  import Icon from './Icon.svelte'
  import Skeleton from './Skeleton.svelte'
  import StatusBadge from './StatusBadge.svelte'
  import CronField from './CronField.svelte'
  import Prose from './Prose.svelte'
  import { describeCron } from '../lib/schedule'
  import type { AgentInfo, ProjectDetail, RunRecord } from '../lib/types'

  // `home` = the project page (configure: editable Knowledge + Scheduled open,
  // no live Progress). `chat` = beside a conversation (work: Progress on top,
  // config collapsed to reference).
  let {
    projectId,
    onrenamed,
    mode = 'home',
    sessionId = null,
  }: {
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
  let saving = $state(false)
  let newMemory = $state('')
  let pinning = $state(false)

  let showSchedForm = $state(false)
  let schedAdding = $state(false)
  let sName = $state('')
  let sAgent = $state('orchestrator')
  let sCron = $state('0 8 * * *')
  let sTask = $state('')

  // Snapshot of the last server values seeded into the form — an activityTick
  // reload (a background run finishing, any bumpActivity) must refresh `data`
  // without replacing text the user is mid-way through typing.
  let seeded = { name: '', description: '', instructions: '', workDir: '' }
  const formDirty = () =>
    name !== seeded.name ||
    description !== seeded.description ||
    instructions !== seeded.instructions ||
    workDir !== seeded.workDir
  function seedForm() {
    if (!data) return
    name = data.project.name
    description = data.project.description
    instructions = data.project.instructions
    workDir = data.project.work_dir ?? ''
    seeded = { name, description, instructions, workDir }
  }

  async function load(reseedForm = false) {
    const pid = projectId
    try {
      const d = await api.projectDetail(pid)
      if (projectId !== pid) return // rail switched projects while awaiting
      data = d
      // Reset the form on project change (reseedForm) or when it holds no
      // unsaved edits; a dirty form only ever loses its text via explicit save.
      if (reseedForm || !formDirty()) seedForm()
    } catch {
      /* ignore — rail just stays empty */
    } finally {
      railLoading = false
    }
  }
  let loadedProjectId: string | null = null
  $effect(() => {
    void workspace.activityTick // reload Progress when a chat/run completes
    const projectChanged = projectId !== loadedProjectId
    loadedProjectId = projectId
    void load(projectChanged)
  })
  $effect(() => {
    void api
      .agents()
      .then((a) => (agents = a))
      .catch(() => {})
  })
  // Chat-scoped Progress: this conversation's background runs only (reloads when
  // a turn finishes via activityTick). Empty for a brand-new, unsaved chat.
  async function loadChatRuns(sid: string) {
    try {
      const r = await api.runs({ session_id: sid, exclude_chats: true, limit: 8 })
      if (sessionId === sid) chatRuns = r
    } catch {
      /* keep what we have */
    }
  }
  $effect(() => {
    void workspace.activityTick
    const sid = sessionId
    if (mode !== 'chat' || !sid) {
      chatRuns = []
      return
    }
    void loadChatRuns(sid)
  })
  // While any listed run is still going, poll so the rail advances on its own
  // (spawned runs finish without a chat turn to bump activityTick). The timer
  // stops as soon as nothing is live, on unmount, and while the tab is hidden.
  const anyLive = $derived(chatRuns.some((r) => r.status === 'running' || r.status === 'pending'))
  $effect(() => {
    const sid = sessionId
    if (mode !== 'chat' || !sid || !anyLive) return
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') void loadChatRuns(sid)
    }, 5000)
    return () => clearInterval(timer)
  })

  // --- Summary: written by the summariser from the project's own activity ---
  let summarising = $state(false)
  let dailyBusy = $state(false)
  async function refreshSummary() {
    if (summarising) return
    summarising = true
    try {
      const run = await api.summariseProject(projectId)
      // One short run; poll it so the rail updates itself when the brief lands.
      for (let i = 0; i < 80; i++) {
        await new Promise((r) => setTimeout(r, 3000))
        const r = await api.run(run.id)
        if (r.status !== 'running' && r.status !== 'pending') {
          if (r.status !== 'completed')
            toast(r.error_summary?.title ?? r.error ?? 'The summary run did not finish', 'err')
          break
        }
      }
      await load()
      bumpActivity()
    } catch (e) {
      toast(errorText(e), 'err')
    } finally {
      summarising = false
    }
  }
  async function toggleDaily(e: Event) {
    const on = (e.currentTarget as HTMLInputElement).checked
    dailyBusy = true
    try {
      await api.setDailySummary(projectId, on)
      toast(on ? 'Daily summary on: every evening at 18:00' : 'Daily summary off', 'ok')
      await load()
    } catch (err) {
      toast(errorText(err), 'err')
    } finally {
      dailyBusy = false
    }
  }

  async function save() {
    if (saving) return
    saving = true
    saveError = ''
    try {
      const saved = await api.saveProject(projectId, { name, description, instructions, work_dir: workDir })
      workDir = saved.work_dir // server echoes the resolved path
      seeded = { name, description, instructions, workDir } // form is clean again
    } catch (e) {
      // Most commonly a 400 from working-folder validation (bad/missing path).
      saveError = e instanceof Error ? e.message : 'Save failed'
      return
    } finally {
      saving = false
    }
    savedFlash = true
    onrenamed?.(name)
    void loadProjects() // keep names fresh elsewhere (sidebar, breadcrumb)
    setTimeout(() => (savedFlash = false), 2200)
  }
  async function addMem() {
    const t = newMemory.trim()
    if (!t || pinning) return
    pinning = true
    try {
      await api.addMemory(projectId, t)
    } catch (e) {
      toast(errorText(e), 'err')
      return
    } finally {
      pinning = false
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
    if (!sName.trim() || !sCron.trim() || schedAdding) return
    schedAdding = true
    try {
      await api.newSchedule({
        name: sName.trim(),
        agent: sAgent,
        cron: sCron.trim(),
        task: sTask,
        project_id: projectId,
      })
    } catch (e) {
      // Most often a bad cron or a duplicate runner name (400) — keep the form
      // open so the user can fix it rather than silently swallowing the error.
      toast(errorText(e), 'err')
      return
    } finally {
      schedAdding = false
    }
    sName = ''
    sTask = ''
    showSchedForm = false
    await load()
  }
  async function removeSchedule(n: string) {
    if (
      !(await confirmDialog(
        `Delete runner "${n}"? Its schedule stops. Past runs and outputs are kept.`,
        'Delete runner',
      ))
    )
      return
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
      fileError = e instanceof Error ? e.message : 'Upload failed'
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
    if (
      !(await confirmDialog(
        `Remove "${name}" from this project? The file is deleted from the project folder.`,
        'Remove file',
      ))
    )
      return
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
        <p class="rail-help">Background runs from this conversation.</p>
        {#if workspace.streaming}
          <div class="prog-live">
            <span class="live-dot sm"></span>
            <span class="text-bright" style="font-size: 12px;">{workspace.liveLabel || 'Working…'}</span>
          </div>
        {/if}
        {#if chatRuns.length}
          <div class="space-y-1">
            {#each chatRuns.slice(0, 8) as r (r.id)}
              <a href={href('/runs/' + r.id)} use:link class="prog-row">
                <span class="prog-name">{r.agent}</span>
                <StatusBadge status={r.status} />
                <span class="prog-meta"
                  >{relativeTime(r.started_at)}{#if r.total_cost_usd}
                    · {fmtCost(r.total_cost_usd)}{/if}</span
                >
              </a>
            {/each}
          </div>
        {:else if !workspace.streaming}
          <div class="rail-empty">No background runs yet.</div>
        {/if}
      </Section>
    {/if}

    <!-- The brief that writes itself: what the project is about and where it
         stands, from its own chats and runs. Every project chat starts from it. -->
    <Section label="Summary" open={mode === 'home' || !!data.project.summary}>
      {#snippet action()}
        {#if mode === 'home'}
          <button class="btn-ghost btn-sm" onclick={refreshSummary} disabled={summarising}>
            {#if summarising}<Icon name="loader" size={13} class="spin" />Writing…{:else}<Icon
                name="refresh"
                size={13}
              />{data?.project.summary ? 'Refresh' : 'Write one'}{/if}
          </button>
        {/if}
      {/snippet}
      {#if data.project.summary}
        <Prose text={data.project.summary} class="reading summary-prose" />
        <div class="summary-meta">
          Written {relativeTime(data.project.summary_at ?? data.project.updated_at)}
          {#if data.daily_summary}· refreshed daily{/if}
        </div>
      {:else}
        <div class="rail-empty">
          No summary yet. {mode === 'home'
            ? "Write one reads this project's chats and runs and leaves a short brief here."
            : 'The project page can write one.'}
        </div>
      {/if}
      {#if mode === 'home'}
        <label class="daily-check">
          <input type="checkbox" checked={!!data.daily_summary} disabled={dailyBusy} onchange={toggleDaily} />
          <span>
            <strong>Refresh every evening</strong>
            <span class="rail-help" style="margin: 0;"
              >One summariser run at 18:00, so tomorrow's chats start informed.</span
            >
          </span>
        </label>
      {/if}
    </Section>

    <!-- One "Knowledge" home for everything the agent knows: the always-on
         instructions (prose) and the discrete pinned facts. Expanded on the
         project page (configure), collapsed beside a chat (reference). -->
    <Section label="Knowledge" open={mode === 'home'}>
      {#if mode === 'home'}
        <!-- Project page: full editable config. -->
        <p class="rail-help">What the agent always knows here — applied to every chat and attached runner.</p>

        <div class="label-cap" style="margin-bottom: 6px;">Instructions · always on</div>
        <textarea
          id="project-instructions"
          class="field"
          bind:value={instructions}
          rows="7"
          aria-label="Project instructions"
          placeholder="How the agent should behave in this project…"
          style="resize: vertical; font-size: 13px;"></textarea>
        <div style="display: flex; align-items: center; gap: 10px; margin-top: 8px;">
          <button class="btn-primary" disabled={saving} onclick={save}>{saving ? 'Saving…' : 'Save'}</button>
          {#if savedFlash}<span class="saved-flash"><Icon name="check" size={13} />Saved</span>{/if}
        </div>

        <div class="rail-divider"></div>

        <div class="label-cap" style="margin-bottom: 6px;">Pinned facts</div>
        <p class="rail-help">Discrete facts — added here, via "remember that…", or /remember.</p>
        {#if data.memories.length}
          <div class="space-y-2" style="margin-bottom: 8px;">
            {#each data.memories as m (m.id)}
              <div class="mem-row">
                <span class="mem-text">{m.text}</span>
                <button
                  class="btn-icon danger"
                  onclick={() => removeMem(m.id)}
                  title="Forget this fact"
                  aria-label="Forget this fact"
                >
                  <Icon name="x" size={13} />
                </button>
              </div>
            {/each}
          </div>
        {:else}
          <div class="rail-empty" style="margin-bottom: 8px;">No pinned facts yet.</div>
        {/if}
        <div style="display: flex; gap: 6px;">
          <input
            class="field"
            bind:value={newMemory}
            placeholder="Pin a fact…"
            aria-label="Pin a fact"
            onkeydown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addMem()
              }
            }}
          />
          <button class="btn-primary" disabled={pinning} onclick={addMem}
            ><Icon name="pin" size={13} />Pin</button
          >
        </div>
      {:else}
        <!-- Beside a chat: read-only reference. Edit on the project page. -->
        <div class="label-cap" style="margin-bottom: 6px;">Instructions · always on</div>
        {#if instructions.trim()}
          <div class="ref-text">{instructions}</div>
        {:else}
          <div class="rail-empty">None set.</div>
        {/if}

        <div class="label-cap" style="margin: 12px 0 6px;">Pinned facts</div>
        {#if data.memories.length}
          <ul class="mem-list">
            {#each data.memories as m (m.id)}
              <li>{m.text}</li>
            {/each}
          </ul>
        {:else}
          <div class="rail-empty">No pinned facts yet.</div>
        {/if}

        <a href={href('/projects/' + encodeURIComponent(projectId))} use:link class="ref-edit">
          <Icon name="pencil" size={12} />Edit in project
        </a>
      {/if}
    </Section>

    {#if mode === 'home'}
      <Section label="Scheduled" open={true}>
        {#snippet action()}
          <button class="btn-ghost btn-sm" onclick={() => (showSchedForm = !showSchedForm)}
            ><Icon name="plus" size={13} />Add</button
          >
        {/snippet}
        <p class="rail-help">Recurring runners for this project.</p>
        {#if showSchedForm}
          <div class="card-quiet" style="margin-bottom: 8px;">
            <input
              class="field"
              bind:value={sName}
              placeholder="Name (unique)"
              aria-label="Runner name"
              style="margin-bottom: 6px;"
            />
            <select class="field" bind:value={sAgent} aria-label="Agent" style="margin-bottom: 6px;">
              {#each agents as a (a.name)}<option value={a.name}>{a.name}</option>{/each}
            </select>
            <div style="margin-bottom: 6px;"><CronField bind:value={sCron} /></div>
            <textarea
              class="field"
              bind:value={sTask}
              rows="4"
              aria-label="Runner task"
              placeholder="What should the agent do each run?"
              style="resize: vertical; line-height: 1.5;"></textarea>
            <div style="display: flex; gap: 6px; justify-content: flex-end; margin-top: 8px;">
              <button class="btn-ghost" disabled={schedAdding} onclick={() => (showSchedForm = false)}
                >Cancel</button
              >
              <button class="btn-primary" disabled={schedAdding} onclick={addSchedule}
                >{schedAdding ? 'Adding…' : 'Add runner'}</button
              >
            </div>
          </div>
        {/if}
        {#if data.schedules.length}
          <div class="space-y-2">
            {#each data.schedules as s (s.name)}
              <div class="sched-row">
                <span class="text-bright">{s.name}</span>
                <span class="text-dim" title={s.cron}>{describeCron(s.cron)}</span>
                <button
                  class="btn-icon danger ml-auto"
                  onclick={() => removeSchedule(s.name)}
                  title="Delete runner"
                  aria-label="Delete runner {s.name}"><Icon name="trash" size={13} /></button
                >
              </div>
            {/each}
          </div>
        {:else}
          <div class="rail-empty">No runners yet.</div>
        {/if}
      </Section>
    {:else if data.schedules.length}
      <!-- Beside a chat: read-only list of runners. -->
      <Section label="Scheduled" open={false}>
        <div class="space-y-2">
          {#each data.schedules as s (s.name)}
            <div class="sched-row">
              <span class="text-bright">{s.name}</span>
              <span class="text-dim" title={s.cron}>{describeCron(s.cron)}</span>
            </div>
          {/each}
        </div>
      </Section>
    {/if}

    <Section label="Files" open={mode === 'home'}>
      <p class="rail-help">Uploaded files agents in this project can read on demand (PDFs, notes, data).</p>

      {#if data.files.length}
        <div class="space-y-2" style="margin-bottom: 10px;">
          {#each data.files as f (f.name)}
            <div class="file-row">
              <a
                class="file-name mono"
                href={fileUrl(f.name)}
                target="_blank"
                rel="noopener"
                title="Open {f.name} in a new tab"><Icon name="file" size={12} />{f.name}</a
              >
              <span class="file-size">{f.size_human}</span>
              {#if mode === 'home'}
                <button
                  class="btn-icon danger"
                  title="Remove file"
                  onclick={() => removeFile(f.name)}
                  aria-label="Remove {f.name}"><Icon name="x" size={13} /></button
                >
              {/if}
            </div>
          {/each}
        </div>
      {:else}
        <div class="rail-empty" style="margin-bottom: 10px;">No files yet.</div>
      {/if}

      {#if mode === 'home'}
        <!-- Working folder: a real directory agents may read AND write — the
             Cowork-style "do file work where I work". Distinct from the
             managed uploads above (read-only material the user provides). -->
        <div class="rail-divider"></div>
        <div class="label-cap" style="margin-bottom: 6px;">Working folder · read + write</div>
        <p class="rail-help">
          Agents create deliverables here (reports, CSVs, edited copies). Absolute path as the server sees it,
          e.g. <code>/mnt/c/Users/you/Documents/reports</code>.
        </p>
        <div style="display: flex; gap: 6px;">
          <input
            class="field mono"
            bind:value={workDir}
            placeholder="No working folder"
            aria-label="Working folder path"
            style="font-size: 11px;"
            onkeydown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                save()
              }
            }}
          />
          <button class="btn-primary" disabled={saving} onclick={save}
            ><Icon name="folder" size={13} />{saving ? 'Saving…' : 'Set folder'}</button
          >
        </div>
        {#if saveError}<div class="rail-error">{saveError}</div>{/if}
        {#if savedFlash}<div class="saved-flash" style="margin-top: 6px;">
            <Icon name="check" size={13} />Saved
          </div>{/if}

        <div class="rail-divider"></div>
        <div
          class="dropzone"
          class:over={dragOver}
          role="button"
          tabindex="0"
          onclick={() => fileInput?.click()}
          onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && fileInput?.click()}
          ondragover={(e) => {
            e.preventDefault()
            dragOver = true
          }}
          ondragleave={() => (dragOver = false)}
          ondrop={onDrop}
        >
          <Icon name="upload" size={14} />
          {#if uploading}Uploading…{:else}<span class="text-dim">Drop files here, or</span>
            <span class="text-bright">browse</span>{/if}
        </div>
        <input bind:this={fileInput} type="file" multiple style="display: none;" onchange={onPick} />
        {#if fileError}<div class="rail-error">{fileError}</div>{/if}
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
        <input
          class="field"
          bind:value={name}
          placeholder="Name"
          aria-label="Project name"
          style="margin-bottom: 10px;"
        />
        <div class="label-cap" style="margin-bottom: 6px;">Description</div>
        <input
          class="field"
          bind:value={description}
          placeholder="Description"
          aria-label="Project description"
          style="margin-bottom: 10px;"
        />
        <div style="display: flex; align-items: center; gap: 10px;">
          <button class="btn-primary" onclick={save}>Save</button>
          {#if savedFlash}<span class="saved-flash"><Icon name="check" size={13} />Saved</span>{/if}
        </div>
      </Section>
    {/if}
  </div>
{/if}

<style>
  .rail-help {
    font-size: 12px;
    line-height: 1.45;
    color: var(--muted);
    margin-bottom: 8px;
  }
  .rail-help code {
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .rail-empty {
    font-size: 12px;
    color: var(--muted);
  }
  .rail-error {
    color: var(--red);
    font-size: 12px;
    margin-top: 6px;
  }
  .saved-flash {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    font-weight: 500;
    color: var(--green);
  }

  /* Pinned facts */
  .mem-row {
    display: flex;
    gap: 6px;
    align-items: flex-start;
    font-size: 13px;
  }
  .mem-text {
    flex: 1;
    line-height: 1.45;
    color: var(--fg);
  }
  .mem-list {
    margin: 0;
    padding-left: 18px;
    font-size: 13px;
    line-height: 1.45;
    color: var(--fg);
  }
  .mem-list li {
    margin-bottom: 4px;
  }
  .mem-list li::marker {
    color: var(--muted);
  }

  .sched-row {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 13px;
    min-height: 24px;
  }

  /* Working-files list + dropzone. */
  .file-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    min-height: 24px;
  }
  .file-name {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--fg);
    font-size: 12.5px;
  }
  a.file-name:hover {
    color: var(--accent-soft);
    text-decoration: underline;
  }
  .file-size {
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .dropzone {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    border: 1px dashed var(--border2);
    border-radius: 8px;
    padding: 12px 10px;
    text-align: center;
    font-size: 12px;
    color: var(--muted);
    cursor: pointer;
    transition:
      border-color var(--dur-1),
      background var(--dur-1);
  }
  .dropzone:hover {
    border-color: var(--accent);
    color: var(--fg);
  }
  .dropzone.over {
    border-color: var(--accent);
    background: var(--accent-glow);
  }

  /* Read-only instructions preview in the chat rail. */
  .ref-text {
    font-size: 12.5px;
    line-height: 1.5;
    color: var(--fg);
    white-space: pre-wrap;
    max-height: 180px;
    overflow-y: auto;
  }
  .ref-edit {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-top: 12px;
    font-size: 12px;
    font-weight: 500;
  }
  .rail-divider {
    height: 1px;
    background: var(--border);
    margin: 14px 0;
  }
  :global(.summary-prose) {
    font-size: 14px;
    max-height: 320px;
    overflow-y: auto;
    padding-right: 4px;
  }
  .summary-meta {
    font-size: 11.5px;
    color: var(--muted);
    margin-top: 8px;
  }
  .daily-check {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
    font-size: 13px;
    color: var(--fg-dim);
    cursor: pointer;
  }
  .daily-check input {
    cursor: pointer;
    margin-top: 2px;
  }
  .daily-check strong {
    display: block;
    color: var(--fg-bright);
    font-weight: 500;
  }
  :global(.spin) {
    animation: rail-spin 0.8s linear infinite;
  }
  @keyframes rail-spin {
    to {
      transform: rotate(360deg);
    }
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
    margin: 0 -8px;
    border-radius: var(--radius);
    text-decoration: none;
    transition: background var(--dur-1) var(--transition);
  }
  .prog-row:hover {
    background: var(--surface2);
    text-decoration: none;
  }
  .prog-name {
    flex: 1;
    min-width: 0;
    font-size: 13px;
    font-weight: 500;
    color: var(--fg-bright);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .prog-meta {
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
</style>
