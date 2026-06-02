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
from bran.dashboard_data import format_countdown, list_outputs, today_stats, upcoming_schedules
from bran.persistence import (
    INBOX_PROJECT_ID,
    ChatRecord,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    count_chats_per_project,
    delete_chat,
    delete_project,
    delete_schedule,
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
    """The per-chat system-prompt suffix injected for a project chat: the
    project's instructions plus a header telling the agent how to pin memory."""
    body = (project.instructions or "").strip()
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
    if body:
        parts.append("## Project memory\n" + body)
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

@router.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    upcoming = [
        {"name": u.name, "agent": u.agent, "cron": u.cron,
         "next_run": u.next_run.isoformat() if u.next_run else None,
         "countdown": format_countdown(u.next_run, now) if u.next_run else "—"}
        for u in upcoming_schedules(now=now, limit=3)
    ]
    buckets = [
        {"label": b.label, "items": [
            {"kind": i.kind, "title": i.title, "snippet": i.snippet, "agent": i.agent,
             "time_label": i.time_label, "href": i.href}
            for i in b.items
        ]}
        for b in list_outputs(limit=60, now=now)
    ]
    return {
        "stats": asdict(today_stats(now=now)),
        "upcoming": upcoming,
        "buckets": buckets,
        "today_label": now.strftime("%A, %d %B %Y"),
    }


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@router.get("/runs")
async def runs(
    agent: str | None = None, status_: str | None = None,
    project_id: str | None = None, limit: int = 200,
) -> list[dict[str, Any]]:
    return [
        asdict(r)
        for r in list_runs(agent=agent or None, status=status_ or None,
                           project_id=project_id or None, limit=limit)
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
) -> dict[str, Any]:
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    record = RunRecord.new(agent=agent, task=task, project_id=project_id or None)
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
# Schedules / briefings
# ---------------------------------------------------------------------------

@router.get("/schedules")
async def schedules(project_id: str | None = None) -> list[dict[str, Any]]:
    return [asdict(s) for s in list_schedules(project_id=project_id or None)]


@router.post("/schedules")
async def new_schedule(
    name: Annotated[str, Form()],
    agent: Annotated[str, Form()],
    cron: Annotated[str, Form()],
    task: Annotated[str, Form()] = "",
    project_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    rec = ScheduleRecord.new(name=name, agent=agent, task=task, cron=cron,
                             project_id=project_id or None)
    insert_schedule(rec)
    # Register with the live scheduler if one is running (lazy import so the
    # web module doesn't pull APScheduler in when --no-scheduler is used).
    try:
        from bran.scheduler import register_schedule

        register_schedule(rec)
    except Exception:
        pass
    return asdict(rec)


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


@router.get("/briefings")
async def briefings() -> list[dict[str, Any]]:
    files = sorted(SETTINGS.briefings_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size_kb": round(p.stat().st_size / 1024, 1), "mtime": p.stat().st_mtime} for p in files]


@router.get("/briefings/{filename}")
async def briefing_detail(filename: str) -> dict[str, Any]:
    if "/" in filename or "\\" in filename or filename.startswith(".."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid filename")
    path = SETTINGS.briefings_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No briefing {filename}")
    return {"name": filename, "content": path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def _project_dict(p: ProjectRecord, n_chats: int) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "instructions": p.instructions, "n_chats": n_chats,
        "updated_at": p.updated_at, "is_inbox": p.id == INBOX_PROJECT_ID,
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
        "schedules": [asdict(s) for s in list_schedules(project_id=project_id)],
        "runs": [asdict(r) for r in list_runs(project_id=project_id, limit=50)],
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
    if project_id == INBOX_PROJECT_ID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Inbox cannot be deleted")
    if not delete_project(project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    return {"deleted": project_id}


@router.post("/projects/{project_id}/memory/append")
async def append_project_memory(
    project_id: str, text: Annotated[str, Form()],
) -> dict[str, Any]:
    """Append a chunk to a project's memory (the chat 'pin to memory' button)."""
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    text = text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "text required")
    sep = "\n\n" if (project.instructions or "").strip() else ""
    project.instructions = (project.instructions or "") + sep + text
    update_project(project)
    return {"ok": True, "project_id": project_id, "memory_length": len(project.instructions)}


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

    if sid:
        existing = get_chat(sid)
        target_project_id = existing.project_id if existing else (project_id or INBOX_PROJECT_ID)
    else:
        target_project_id = project_id or INBOX_PROJECT_ID

    project_record = get_project(target_project_id)
    if project_record is None:
        target_project_id = INBOX_PROJECT_ID
        project_record = get_project(INBOX_PROJECT_ID)
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
