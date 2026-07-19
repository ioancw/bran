"""JSON API for the Svelte SPA (token-free, localhost-first; same-origin only).

This is the single contract the frontend consumes. Chat live-stream and replay
both emit the unified schema from `bran.web.events`, so the client renders them
through one path. There is no bearer token on this surface, but every route is
gated by `_require_same_origin` so other websites in the user's browser can't
fire requests at it (CSRF).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from bran.agents import get_agent, list_agents
from bran.background import spawn_background
from bran.config import SETTINGS
from bran.persistence import (
    ChatRecord,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    add_project_memory,
    count_chats_per_project,
    delete_chat,
    delete_project,
    delete_project_memory,
    delete_schedule,
    get_chat,
    get_output_states,
    get_project,
    get_run,
    get_setting,
    insert_project,
    insert_run,
    insert_schedule,
    list_chats,
    list_project_memories,
    list_projects,
    list_runs,
    list_schedules,
    mark_outputs_read,
    move_chat_to_project,
    set_output_starred,
    set_schedule_enabled,
    set_setting,
    touch_chat,
    update_project,
    upsert_chat,
)
from bran.project_files import (
    delete_file as delete_project_file,
)
from bran.project_files import (
    files_prompt,
    save_attachment,
    workdir_prompt,
)
from bran.project_files import (
    list_files as list_project_files,
)
from bran.project_files import (
    save_upload as save_project_file,
)
from bran.runner import run_agent, stream_agent
from bran.transcript import find_session_file, parse_transcript
from bran.web.events import events_from_message, events_from_transcript_entry


async def _require_same_origin(request: Request) -> None:
    """CSRF guard for the unauthenticated SPA surface.

    /spa is token-free (localhost-first), but several POST handlers take form
    bodies — CORS "simple requests" that any web page the user visits can fire
    at 127.0.0.1 without a preflight, silently starting agent runs or creating
    schedules. Browsers tell us where a request came from, so reject anything
    cross-site: via Sec-Fetch-Site when present (all modern browsers), else by
    comparing the Origin header's host against the request Host. Requests with
    neither header come from non-browser clients (curl, httpx) where CSRF
    doesn't apply.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        # "none" = direct navigation (address bar / bookmark) — user-initiated.
        if site not in ("same-origin", "none"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Cross-origin requests are not allowed"
            )
        return
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit

        # "null" (sandboxed iframe, data: URI) is also cross-origin.
        if origin == "null" or urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Cross-origin requests are not allowed"
            )


router = APIRouter(
    prefix="/spa", tags=["spa"], dependencies=[Depends(_require_same_origin)]
)


# ---------------------------------------------------------------------------
# Chat routing / project-context helpers (moved here at SPA cutover, when the
# legacy routes.py was deleted — this is now their only home).
# ---------------------------------------------------------------------------

def _list_slash_commands() -> list[dict[str, str]]:
    """Discover slash commands from `.claude/commands/*.md` for autocomplete."""
    out: list[dict[str, str]] = []
    cmd_dir = SETTINGS.claude_dir / "commands"
    if not cmd_dir.exists():
        return out
    for p in sorted(cmd_dir.glob("*.md")):
        desc = ""
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if m:
            desc = m.group(1).strip().strip("\"'")
        else:
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(("---", "name:", "argument-hint:", "allowed-tools:")):
                    desc = line[:120]
                    break
        out.append({"name": p.stem, "description": desc})
    return out


def _build_project_context(project: ProjectRecord) -> str:
    """The per-chat system-prompt suffix for a project: the brief (instructions),
    the discrete memory entries, and a header on how to pin new memory."""
    header_lines = [
        "## Project context",
        f'You are chatting in bran project "{project.name}" (id: `{project.id}`).',
        (
            "If the user says 'remember that…', invokes the /remember slash "
            "command, or you decide something is worth persisting across "
            "future chats in this project, call the "
            "`mcp__bran__save_project_memory` tool with project_id=\"" +
            project.id + '" and a concise `text`.'
        ),
    ]
    parts = ["\n".join(header_lines)]
    brief = (project.instructions or "").strip()
    if brief:
        parts.append("## Instructions\n" + brief)
    memories = list_project_memories(project.id)
    if memories:
        parts.append("## Memory\n" + "\n".join(f"- {m.text}" for m in memories))
    files = files_prompt(project.id)
    if files:
        parts.append(files)
    workdir = workdir_prompt(project.work_dir)
    if workdir:
        parts.append(workdir)
    return "\n\n".join(parts)


def _parse_agent_prefix(prompt: str) -> tuple[str, str]:
    """Detect `@name <rest>` / `@agent-name <rest>`; fall back to orchestrator."""
    m = re.match(r"^@(?:agent-)?([\w-]+)\s+(.*)", prompt, re.DOTALL)
    if not m:
        return ("orchestrator", prompt)
    candidate = m.group(1)
    if candidate in {a.name for a in list_agents()}:
        return (candidate, m.group(2).strip())
    return ("orchestrator", prompt)


# ---------------------------------------------------------------------------
# Catalog / agents
# ---------------------------------------------------------------------------

@router.get("/catalog")
async def catalog() -> dict[str, Any]:
    """Agents + slash commands for the chat composer's autocomplete."""
    return {
        "agents": [{"name": a.name, "description": a.description} for a in list_agents()],
        "commands": _list_slash_commands(),
    }


@router.get("/agents")
async def agents() -> list[dict[str, Any]]:
    return [
        {"name": a.name, "description": a.description, "model": a.model,
         "tools": a.tools, "subagents": list(a.subagents)}
        for a in list_agents()
    ]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@router.get("/runs")
async def runs(
    agent: str | None = None, status_: str | None = None,
    project_id: str | None = None, schedule_id: str | None = None,
    exclude_chats: bool = False, limit: int = 200, q: str | None = None,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    from bran.persistence import list_artifacts_for_runs

    records = list_runs(agent=agent or None, status=status_ or None,
                        project_id=project_id or None,
                        schedule_id=schedule_id or None,
                        exclude_chats=exclude_chats, limit=limit, q=q or None,
                        session_id=session_id or None)
    ids = [r.id for r in records]
    artifacts = list_artifacts_for_runs(ids)
    states = get_output_states(ids)
    out = []
    for r in records:
        d = asdict(r)
        d["artifacts"] = artifacts.get(r.id, [])
        st = states.get(r.id)
        d["starred"] = bool(st["starred"]) if st else False
        d["read_at"] = st["read_at"] if st else None
        out.append(d)
    return out


@router.get("/runs/{run_id}")
async def run_detail(run_id: str) -> dict[str, Any]:
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    return asdict(rec)


# --- Output reading state (star / read) — durable, cross-browser ----------

def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


@router.post("/outputs/{run_id}/star")
async def star_output(
    run_id: str, starred: Annotated[str, Form()] = "true",
) -> dict[str, Any]:
    if get_run(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    want = _truthy(starred)
    set_output_starred(run_id, want)
    return {"run_id": run_id, "starred": want}


@router.post("/outputs/read")
async def mark_read(ids: Annotated[str, Form()] = "") -> dict[str, Any]:
    """Mark a comma-separated batch of runs read (opening the Outputs inbox)."""
    run_ids = [x for x in (s.strip() for s in ids.split(",")) if x]
    mark_outputs_read(run_ids)
    return {"read": len(run_ids)}


@router.post("/runs")
async def new_run(
    agent: Annotated[str, Form()],
    task: Annotated[str, Form()],
    project_id: Annotated[str | None, Form()] = None,
    schedule_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    record = RunRecord.new(
        agent=agent, task=task, project_id=project_id or None,
        schedule_id=schedule_id or None,
    )
    insert_run(record)

    async def _go():
        try:
            await run_agent(agent, task, record=record)
        except Exception:
            pass  # runner persists the failure

    spawn_background(_go(), name=f"spa-run:{record.id}")
    return asdict(record)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    from bran.background import cancel_background

    if get_run(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    return {"run_id": run_id, "cancelled": cancel_background(run_id)}


@router.get("/runs/{run_id}/transcript")
async def run_transcript(run_id: str) -> dict[str, Any]:
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    events: list[dict[str, Any]] = []
    if rec.session_id:
        path = find_session_file(rec.session_id)
        if path is not None:
            for entry in parse_transcript(path):
                events.extend(events_from_transcript_entry(entry))
    return {"run": asdict(rec), "events": events}


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: str) -> StreamingResponse:
    """Watch a background run execute: SSE of its unified events, live.

    Subscribers attaching mid-run get a full replay first (see bran.live). If
    the run already finished — or isn't live in this process (server restart)
    — the stream ends immediately with `done` and the client falls back to the
    stored transcript.
    """
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, default=str)}\n\n"

    async def gen():
        from bran.live import is_live, subscribe

        if rec.status in ("running", "pending") and is_live(run_id):
            async for ev in subscribe(run_id):
                yield sse(ev)
        yield sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Artifacts — files a run produced (captured from its Write/Edit tool calls)
# ---------------------------------------------------------------------------

def _artifact_entries(rec: RunRecord) -> list[dict[str, Any]]:
    """Existence-checked listing of a run's recorded artifacts."""
    from bran.persistence import list_artifacts

    out: list[dict[str, Any]] = []
    for i, raw in enumerate(list_artifacts(rec.id)):
        p = Path(raw)
        exists = p.is_file()
        out.append({
            "index": i,
            "name": p.name,
            "path": str(p),
            "exists": exists,
            "size": p.stat().st_size if exists else None,
        })
    return out


@router.get("/runs/{run_id}/artifacts")
async def run_artifacts(run_id: str) -> list[dict[str, Any]]:
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    return _artifact_entries(rec)


@router.get("/runs/{run_id}/artifacts/{index}")
async def download_artifact(run_id: str, index: int) -> FileResponse:
    """Download one recorded artifact by its position in the run's list.

    Index-based (not path-based) so no client-supplied path ever reaches the
    filesystem. Defense in depth: even the *recorded* path must still resolve
    inside the run's sanctioned write roots at serve time — guards against a
    stale work_dir being repointed somewhere sensitive after the run.
    """
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    from bran.persistence import list_artifacts

    entries = list_artifacts(run_id)
    if not (0 <= index < len(entries)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No artifact #{index} on run {run_id}")
    from bran.permissions import write_roots_for_project

    p = Path(entries[index]).resolve()
    roots = write_roots_for_project(rec.project_id)
    if not any(p == r or r in p.parents for r in roots):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "artifact path no longer sanctioned")
    if not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"artifact no longer exists: {p.name}")
    return FileResponse(p, filename=p.name)


# ---------------------------------------------------------------------------
# App settings — the global "About me" instructions (every agent run sees them)
# ---------------------------------------------------------------------------

@router.get("/settings")
async def app_settings() -> dict[str, Any]:
    return {"user_instructions": get_setting("user_instructions")}


@router.post("/settings")
async def save_app_settings(
    user_instructions: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    set_setting("user_instructions", user_instructions)
    return {"user_instructions": user_instructions}


# ---------------------------------------------------------------------------
# Schedules (Runners)
# ---------------------------------------------------------------------------

def _schedule_dict(s: ScheduleRecord) -> dict[str, Any]:
    """A schedule plus its computed next fire time (None when paused/invalid).
    One-shot runners (`run_at` set) fire once: next_run is run_at while it's
    still in the future, else None."""
    d = asdict(s)
    next_run: str | None = None
    if s.enabled:
        if s.run_at:
            from datetime import datetime, timezone

            try:
                when = datetime.fromisoformat(s.run_at)
                if when.tzinfo is None:
                    # Naive run_at (the conversational create_runner path stores
                    # these) means server-local time — same as DateTrigger reads
                    # it. Make it aware, or the comparison below raises TypeError.
                    when = when.astimezone()
                next_run = s.run_at if when > datetime.now(timezone.utc) else None
            except (ValueError, TypeError):
                next_run = None
        else:
            try:
                from bran.scheduler import next_run_for

                next_run = next_run_for(s.cron)
            except Exception:
                next_run = None
    d["next_run"] = next_run
    return d


def _normalize_cron(expr: str) -> str:
    """Resolve a schedule field — natural language ('every weekday at 8am') OR a
    raw 5-field cron — to a canonical cron string, via nl_cron. Raises a 400 with
    a recoverable suggestion if it can't be parsed."""
    from bran.nl_cron import NlCronParseError, parse

    try:
        return parse(expr).cron
    except NlCronParseError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/schedules/parse")
async def parse_schedule(expr: str) -> dict[str, Any]:
    """Interpret a schedule string (natural language or cron) without saving —
    powers the live 'this is when it'll run' preview in the schedule forms."""
    from bran.nl_cron import NlCronParseError, parse

    try:
        r = parse(expr)
        return {"ok": True, "cron": r.cron, "human": r.human, "error": ""}
    except NlCronParseError as e:
        return {"ok": False, "cron": "", "human": "", "error": str(e)}


@router.get("/schedules")
async def schedules(project_id: str | None = None) -> list[dict[str, Any]]:
    return [_schedule_dict(s) for s in list_schedules(project_id=project_id or None)]


def _form_flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@router.post("/schedules")
async def new_schedule(
    name: Annotated[str, Form()],
    agent: Annotated[str, Form()],
    cron: Annotated[str, Form()] = "",
    task: Annotated[str, Form()] = "",
    project_id: Annotated[str | None, Form()] = None,
    run_at: Annotated[str | None, Form()] = None,
    verify: Annotated[str, Form()] = "",
    delta: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    run_at = (run_at or "").strip() or None
    if run_at:
        # One-shot: validate the datetime; cron is unused for these.
        from datetime import datetime

        try:
            datetime.fromisoformat(run_at)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid run_at: {run_at!r}")
        cron = ""
    elif not cron.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cron or run_at required")
    else:
        cron = _normalize_cron(cron)  # accept natural language too
    rec = ScheduleRecord.new(name=name, agent=agent, task=task, cron=cron,
                             project_id=project_id or None, run_at=run_at,
                             verify=_form_flag(verify), delta=_form_flag(delta))
    insert_schedule(rec)
    # Register with the live scheduler if one is running (lazy import so the
    # web module doesn't pull APScheduler in when --no-scheduler is used).
    try:
        from bran.scheduler import register_schedule

        register_schedule(rec)
    except Exception:
        pass
    return _schedule_dict(rec)


@router.post("/schedules/{name}")
async def edit_schedule(
    name: str,
    agent: Annotated[str, Form()],
    cron: Annotated[str, Form()] = "",
    task: Annotated[str, Form()] = "",
    run_at: Annotated[str | None, Form()] = None,
    verify: Annotated[str | None, Form()] = None,
    delta: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Edit a runner's agent / task / trigger in place and re-sync the live job.

    Name, project, and paused state are preserved (rename/move/pause are separate
    actions). Same validation as create: agent must exist; run_at (if given)
    makes it a one-shot and clears cron, else cron is required."""
    from bran.persistence import get_schedule, update_schedule

    if get_schedule(name) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No schedule {name}")
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    run_at = (run_at or "").strip() or None
    if run_at:
        from datetime import datetime

        try:
            datetime.fromisoformat(run_at)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid run_at: {run_at!r}")
        cron = ""
    elif not cron.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cron or run_at required")
    else:
        cron = _normalize_cron(cron)  # accept natural language too
    rec = update_schedule(
        name, agent=agent, task=task, cron=cron, run_at=run_at,
        # None = field absent from the form = leave unchanged.
        verify=_form_flag(verify) if verify is not None else None,
        delta=_form_flag(delta) if delta is not None else None,
    )
    # Re-sync the live scheduler: drop the old job, add the new one (register is a
    # no-op when the runner is paused, so paused stays paused).
    try:
        from bran.scheduler import register_schedule, unregister_schedule

        unregister_schedule(name)
        register_schedule(rec)
    except Exception:
        pass
    return _schedule_dict(rec)


@router.post("/schedules/{name}/enabled")
async def set_enabled(
    name: str, enabled: Annotated[str, Form()],
) -> dict[str, Any]:
    """Pause/resume a runner and keep the live scheduler in sync."""
    on = enabled.strip().lower() in ("1", "true", "yes", "on")
    rec = set_schedule_enabled(name, on)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No schedule {name}")
    try:
        from bran.scheduler import register_schedule, unregister_schedule

        if on:
            register_schedule(rec)
        else:
            unregister_schedule(name)
    except Exception:
        pass
    return _schedule_dict(rec)


@router.delete("/schedules/{name}")
async def remove_schedule(name: str) -> dict[str, Any]:
    if not delete_schedule(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No schedule {name}")
    try:
        from bran.scheduler import unregister_schedule

        unregister_schedule(name)
    except Exception:
        pass
    return {"deleted": name}


@router.post("/chats/{chat_id}/move")
async def move_chat(chat_id: str, project_id: Annotated[str, Form()]) -> dict[str, Any]:
    if get_project(project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    if not move_chat_to_project(chat_id, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return {"chat_id": chat_id, "project_id": project_id}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def _project_dict(p: ProjectRecord, n_chats: int) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "instructions": p.instructions, "work_dir": p.work_dir,
        "n_chats": n_chats,
        "updated_at": p.updated_at,
    }


@router.get("/projects")
async def projects() -> list[dict[str, Any]]:
    counts = count_chats_per_project()
    return [_project_dict(p, counts.get(p.id, 0)) for p in list_projects()]


@router.get("/projects/{project_id}")
async def project_detail(project_id: str) -> dict[str, Any]:
    """The workspace hub: a project plus everything that flows through it —
    chats, schedules, and recent runs (the project activity)."""
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    chats = list_chats(limit=200, project_id=project_id)
    return {
        "project": _project_dict(project, len(chats)),
        "chats": [
            {"id": c.id, "title": c.title, "agent": c.agent,
             "updated_at": c.updated_at, "created_at": c.created_at}
            for c in chats
        ],
        "memories": [asdict(m) for m in list_project_memories(project_id)],
        "files": list_project_files(project_id),
        "schedules": [asdict(s) for s in list_schedules(project_id=project_id)],
        # Activity = the project's autonomous/background runs (runner fires,
        # spawns) — NOT interactive chat turns, which live in Recents above.
        "runs": [asdict(r) for r in list_runs(project_id=project_id, limit=50, exclude_chats=True)],
    }


@router.post("/projects")
async def new_project(
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is required")
    record = ProjectRecord.new(name=name, description=description.strip())
    insert_project(record)
    return _project_dict(record, 0)


@router.post("/projects/{project_id}")
async def save_project(
    project_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    instructions: Annotated[str, Form()] = "",
    work_dir: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    # Validate the working folder up front so a typo'd path fails the save with
    # a clear message instead of silently granting nothing at run time.
    work_dir = work_dir.strip()
    if work_dir:
        from bran.permissions import validate_work_dir

        try:
            work_dir = str(validate_work_dir(work_dir))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"working folder: {exc}")
    project.name = name.strip() or project.name
    project.description = description.strip()
    project.instructions = instructions
    project.work_dir = work_dir
    update_project(project)
    return _project_dict(project, 0)


@router.delete("/projects/{project_id}")
async def remove_project(project_id: str) -> dict[str, Any]:
    if not delete_project(project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    return {"deleted": project_id}


@router.get("/projects/{project_id}/memory")
async def project_memory(project_id: str) -> list[dict[str, Any]]:
    return [asdict(m) for m in list_project_memories(project_id)]


@router.post("/projects/{project_id}/memory")
async def add_memory(project_id: str, text: Annotated[str, Form()]) -> dict[str, Any]:
    """Pin a discrete memory entry to a project (chat 'pin to memory' button +
    the save_project_memory tool's UI equivalent)."""
    if get_project(project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    text = text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "text required")
    return asdict(add_project_memory(project_id, text))


@router.delete("/projects/{project_id}/memory/{entry_id}")
async def remove_memory(project_id: str, entry_id: str) -> dict[str, Any]:
    if not delete_project_memory(entry_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No memory {entry_id}")
    return {"deleted": entry_id}


# ---------------------------------------------------------------------------
# Project working files — managed-upload folder the agents read on demand.
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file


@router.get("/projects/{project_id}/files")
async def project_files(project_id: str) -> list[dict[str, Any]]:
    if get_project(project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    return list_project_files(project_id)


@router.post("/projects/{project_id}/files")
async def upload_files(
    project_id: str, files: Annotated[list[UploadFile], File()]
) -> list[dict[str, Any]]:
    """Upload one or more files into the project's working folder."""
    if get_project(project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    saved: list[dict[str, Any]] = []
    for f in files:
        data = await f.read()
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{f.filename!r} is {len(data):,} bytes — exceeds the "
                f"{_MAX_UPLOAD_BYTES:,} byte limit",
            )
        try:
            saved.append(save_project_file(project_id, f.filename or "upload", data))
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return saved


@router.post("/uploads")
async def upload_attachments(files: Annotated[list[UploadFile], File()]) -> list[dict[str, Any]]:
    """Chat-composer attachments: one-off files referenced by absolute path in
    the prompt (vs. project files, the per-project library). Saved under
    bran_home/uploads with a unique prefix."""
    saved: list[dict[str, Any]] = []
    for f in files:
        data = await f.read()
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{f.filename!r} is {len(data):,} bytes — exceeds the "
                f"{_MAX_UPLOAD_BYTES:,} byte limit",
            )
        try:
            saved.append(save_attachment(f.filename or "upload", data))
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return saved


@router.get("/projects/{project_id}/files/{filename}")
async def get_project_file(
    project_id: str, filename: str, download: bool = False
) -> FileResponse:
    """Serve one file from the project's folder.

    HTML documents (from `save_document`) open **inline** so the browser renders
    them for print-to-PDF; `?download=1` forces a save-to-disk instead. The name
    is sanitised to a single component and the resolved path re-checked to sit
    inside the folder (defence in depth over `_safe_name`).
    """
    if get_project(project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    from bran.project_files import _safe_name, files_dir

    try:
        safe = _safe_name(filename)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    root = files_dir(project_id).resolve()
    p = (root / safe).resolve()
    if not (p == root or root in p.parents) or not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No file {filename!r}")
    return FileResponse(
        p, filename=safe,
        content_disposition_type="attachment" if download else "inline",
    )


@router.delete("/projects/{project_id}/files/{filename}")
async def remove_file(project_id: str, filename: str) -> dict[str, Any]:
    try:
        ok = delete_project_file(project_id, filename)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No file {filename!r}")
    return {"deleted": filename}


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------

@router.get("/chats")
async def chats(project_id: str | None = None) -> list[dict[str, Any]]:
    return [
        {"id": c.id, "title": c.title, "agent": c.agent, "project_id": c.project_id,
         "updated_at": c.updated_at, "created_at": c.created_at}
        for c in list_chats(limit=100, project_id=project_id)
    ]


@router.get("/chats/{chat_id}")
async def chat_detail(chat_id: str) -> dict[str, Any]:
    """A single chat's metadata — lets the chat view learn its project before
    loading the (project-scoped) sidebar.

    Falls back to runs: a runner/spawn session has no chat row, but its SDK
    session is perfectly resumable. Synthesizing chat metadata from the run
    (same agent, same project) lets "continue this run as a conversation"
    actually resume the session — previously the chat view displayed the
    transcript but, with no chat row, sent the first message into a brand-new
    session, so the agent had no idea what you were referring to.
    """
    c = get_chat(chat_id)
    if c is not None:
        return {"id": c.id, "title": c.title, "agent": c.agent, "project_id": c.project_id,
                "updated_at": c.updated_at, "created_at": c.created_at}
    from bran.persistence import get_run_by_session

    run = get_run_by_session(chat_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    task = (run.task or "").strip()
    title = task[:80].rstrip() + ("…" if len(task) > 80 else "") or f"run {run.id[:8]}"
    return {"id": chat_id, "title": title, "agent": run.agent, "project_id": run.project_id,
            "updated_at": run.ended_at or run.started_at, "created_at": run.started_at}


@router.delete("/chats/{chat_id}")
async def remove_chat(chat_id: str) -> dict[str, Any]:
    if not delete_chat(chat_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return {"deleted": chat_id}


@router.get("/chats/{chat_id}/history")
async def chat_history(chat_id: str) -> dict[str, Any]:
    """Replay a session as unified events (same schema as the live stream)."""
    path = find_session_file(chat_id)
    if path is None:
        return {"session_id": chat_id, "events": []}
    events: list[dict[str, Any]] = []
    for entry in parse_transcript(path):
        events.extend(events_from_transcript_entry(entry))
    return {"session_id": chat_id, "events": events}


@router.post("/chat/stream")
async def chat_stream(
    prompt: Annotated[str, Form()],
    session_id: Annotated[str | None, Form()] = None,
    chat_agent: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
):
    """Stream an agent reply as SSE using the unified event schema.

    Routing precedence: `@agent` mention > the chat's locked agent > orchestrator.
    A chat's project is authoritative once it exists; otherwise the form param,
    else Inbox. The project's instructions are layered onto the system prompt.
    """
    sid = session_id or None
    mentioned_agent, actual_prompt = _parse_agent_prefix(prompt)
    if mentioned_agent != "orchestrator":
        target_agent = mentioned_agent
        sid = None  # don't resume across a different agent
    elif chat_agent and chat_agent in {a.name for a in list_agents()}:
        target_agent = chat_agent
    else:
        target_agent = "orchestrator"

    # The chat's project (None = loose). An existing chat's project is
    # authoritative; otherwise the form param, else loose.
    if sid:
        existing = get_chat(sid)
        target_project_id = existing.project_id if existing else (project_id or None)
    else:
        target_project_id = project_id or None

    project_record = get_project(target_project_id) if target_project_id else None
    if target_project_id and project_record is None:
        target_project_id = None  # stale/unknown id → loose
    append_system = _build_project_context(project_record) if project_record else None

    first_prompt = actual_prompt.strip()
    title_seed = (first_prompt[:80].rstrip() + ("…" if len(first_prompt) > 80 else "")) or "(new chat)"

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, default=str)}\n\n"

    async def event_gen():
        chat_initialised = False
        if mentioned_agent != "orchestrator":
            yield sse({"type": "routed", "agent": target_agent})
        try:
            async for msg in stream_agent(
                target_agent, actual_prompt, resume_session=sid,
                append_system=append_system, project_id=target_project_id,
            ):
                for ev in events_from_message(msg):
                    if not chat_initialised and ev.get("type") == "session" and ev.get("session_id"):
                        sid_now = ev["session_id"]
                        if get_chat(sid_now) is None:
                            upsert_chat(ChatRecord(
                                id=sid_now, title=title_seed,
                                agent=target_agent, project_id=target_project_id,
                            ))
                        else:
                            touch_chat(sid_now)
                        chat_initialised = True
                    yield sse(ev)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the client
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        yield sse({"type": "done"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
