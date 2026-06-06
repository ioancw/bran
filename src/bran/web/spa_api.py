"""JSON API for the Svelte SPA (mounted unauthenticated, localhost-first).

This is the single contract the frontend consumes. Chat live-stream and replay
both emit the unified schema from `bran.web.events`, so the client renders them
through one path. The legacy HTMX routes in `routes.py` are untouched and will
be removed once the SPA takes over `/`.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, status
from fastapi.responses import StreamingResponse

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
    list_project_memories,
    get_chat,
    get_project,
    get_run,
    insert_project,
    insert_run,
    insert_schedule,
    list_chats,
    list_projects,
    list_runs,
    list_schedules,
    move_chat_to_project,
    set_schedule_enabled,
    touch_chat,
    update_project,
    upsert_chat,
)
from bran.runner import run_agent, stream_agent
from bran.transcript import find_session_file, parse_transcript
from bran.web.events import events_from_message, events_from_transcript_entry

router = APIRouter(prefix="/spa", tags=["spa"])


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
    exclude_chats: bool = False, limit: int = 200,
) -> list[dict[str, Any]]:
    return [
        asdict(r)
        for r in list_runs(agent=agent or None, status=status_ or None,
                           project_id=project_id or None,
                           schedule_id=schedule_id or None,
                           exclude_chats=exclude_chats, limit=limit)
    ]


@router.get("/runs/{run_id}")
async def run_detail(run_id: str) -> dict[str, Any]:
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    return asdict(rec)


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
                next_run = s.run_at if when > datetime.now(timezone.utc) else None
            except ValueError:
                next_run = None
        else:
            try:
                from bran.scheduler import next_run_for

                next_run = next_run_for(s.cron)
            except Exception:
                next_run = None
    d["next_run"] = next_run
    return d


@router.get("/schedules")
async def schedules(project_id: str | None = None) -> list[dict[str, Any]]:
    return [_schedule_dict(s) for s in list_schedules(project_id=project_id or None)]


@router.post("/schedules")
async def new_schedule(
    name: Annotated[str, Form()],
    agent: Annotated[str, Form()],
    cron: Annotated[str, Form()] = "",
    task: Annotated[str, Form()] = "",
    project_id: Annotated[str | None, Form()] = None,
    run_at: Annotated[str | None, Form()] = None,
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
    rec = ScheduleRecord.new(name=name, agent=agent, task=task, cron=cron,
                             project_id=project_id or None, run_at=run_at)
    insert_schedule(rec)
    # Register with the live scheduler if one is running (lazy import so the
    # web module doesn't pull APScheduler in when --no-scheduler is used).
    try:
        from bran.scheduler import register_schedule

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
        "instructions": p.instructions, "n_chats": n_chats,
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
) -> dict[str, Any]:
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    project.name = name.strip() or project.name
    project.description = description.strip()
    project.instructions = instructions
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
    loading the (project-scoped) sidebar."""
    c = get_chat(chat_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return {"id": c.id, "title": c.title, "agent": c.agent, "project_id": c.project_id,
            "updated_at": c.updated_at, "created_at": c.created_at}


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
