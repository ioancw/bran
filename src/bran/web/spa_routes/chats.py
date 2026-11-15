"""Chats: the Recents list, a chat's history and sources, and the live
chat stream."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, status
from fastapi.responses import StreamingResponse

from bran.agents import get_agent, list_agents
from bran.background import session_lock
from bran.persistence import (
    ChatRecord,
    delete_chat,
    get_chat,
    get_project,
    get_setting,
    list_chats,
    list_runs,
    move_chat_to_project,
    set_setting,
    touch_chat,
    upsert_chat,
)
from bran.runner import stream_agent
from bran.sources import sources_for_run
from bran.transcript import find_session_file, parse_transcript
from bran.web.events import (
    events_from_message,
    events_from_runs,
    events_from_transcript_entry,
    restored_context,
)
from bran.web.spa_routes.common import attach_child_run, form_flag
from bran.web.spa_routes.projects import build_project_context

router = APIRouter()

# app_settings marker: this chat's history is served from the database even
# though an SDK file exists (see chat_stream's pruned-transcript branch).
DB_HISTORY_KEY = "chat_history_db:"


def delegation_prompt(agent: str) -> str:
    """The per-turn system-prompt note that makes an `@agent …` message a
    delegation to that agent's subagent form — inside the SAME session, so
    the conversation keeps its context instead of forking into a new chat.
    It rides in the system prompt, not the user message, so the transcript
    and the chat title keep the user's own words."""
    return (
        "## Delegation request\n"
        f"For this message, use the `{agent}` subagent (via the Agent tool) to do "
        "the work and relay its answer to the user faithfully."
    )


def parse_agent_prefix(prompt: str) -> tuple[str, str]:
    """Detect `@name <rest>` / `@agent-name <rest>`; fall back to orchestrator."""
    m = re.match(r"^@(?:agent-)?([\w-]+)\s+(.*)", prompt, re.DOTALL)
    if not m:
        return ("orchestrator", prompt)
    candidate = m.group(1)
    if candidate in {a.name for a in list_agents()}:
        return (candidate, m.group(2).strip())
    return ("orchestrator", prompt)


def _chat_dict(c: ChatRecord) -> dict[str, Any]:
    return {"id": c.id, "title": c.title, "agent": c.agent, "project_id": c.project_id,
            "archived_at": c.archived_at,
            "updated_at": c.updated_at, "created_at": c.created_at}


def _chat_turns(session_id: str, limit: int = 1000) -> list[Any]:
    """The conversation's own turns, newest first. list_runs(session_id=…)
    also returns the background runs spawned FROM those turns (the Progress
    rail wants them); for history, restore and sources they must not count."""
    return [r for r in list_runs(session_id=session_id, limit=limit) if r.source in ("chat", "synthesis")]


@router.get("/chats")
def chats(project_id: str | None = None, archived: bool = False) -> list[dict[str, Any]]:
    """Live chats by default; `archived=true` lists the archived ones instead."""
    return [_chat_dict(c) for c in list_chats(limit=100, project_id=project_id, archived=archived)]


@router.post("/chats/{chat_id}/move")
def move_chat(chat_id: str, project_id: Annotated[str, Form()] = "") -> dict[str, Any]:
    """Move a chat into a project, or out of every project (empty id)."""
    target = project_id.strip() or None
    if target is not None and get_project(target) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {target}")
    if not move_chat_to_project(chat_id, target):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return {"chat_id": chat_id, "project_id": target}


@router.post("/chats/{chat_id}/rename")
def rename_chat_endpoint(chat_id: str, title: Annotated[str, Form()]) -> dict[str, Any]:
    from bran.persistence import rename_chat

    clean = " ".join(title.split())[:120]
    if not clean:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A title is required.")
    if not rename_chat(chat_id, clean):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return {"chat_id": chat_id, "title": clean}


@router.post("/chats/{chat_id}/archive")
def archive_chat(chat_id: str, archived: Annotated[str, Form()] = "true") -> dict[str, Any]:
    """Archive a chat (hidden from Recents, kept whole) or restore it."""
    from bran.persistence import set_chat_archived

    if not set_chat_archived(chat_id, form_flag(archived)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    c = get_chat(chat_id)
    return _chat_dict(c) if c else {"id": chat_id}


@router.get("/chats/{chat_id}/sources")
def chat_sources(chat_id: str) -> list[dict[str, Any]]:
    """Every source the conversation's turns cited or fetched, newest turn first."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for turn in _chat_turns(chat_id, limit=40):
        for s in sources_for_run(turn):
            key = s["url"] or ("dom:" + s["domain"])
            if key in seen:
                continue
            seen.add(key)
            out.append({**s, "run_id": turn.id})
        if len(out) >= 60:
            break
    return out


@router.get("/chats/{chat_id}")
def chat_detail(chat_id: str) -> dict[str, Any]:
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
        return _chat_dict(c)
    from bran.persistence import get_run_by_session

    run = get_run_by_session(chat_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    task = (run.task or "").strip()
    title = task[:80].rstrip() + ("…" if len(task) > 80 else "") or f"run {run.id[:8]}"
    return {"id": chat_id, "title": title, "agent": run.agent, "project_id": run.project_id,
            "updated_at": run.ended_at or run.started_at, "created_at": run.started_at}


@router.delete("/chats/{chat_id}")
def remove_chat(chat_id: str) -> dict[str, Any]:
    """Delete a chat together with its own turns (see persistence.delete_chat)."""
    if not delete_chat(chat_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return {"deleted": chat_id}


@router.get("/chats/{chat_id}/history")
def chat_history(chat_id: str) -> dict[str, Any]:
    """Replay a session as unified events (same schema as the live stream)."""
    path = find_session_file(chat_id)
    if path is None or get_setting(DB_HISTORY_KEY + chat_id) == "1":
        # Claude Code prunes session files after ~30 days; the turns live on
        # as chat runs in bran's own database, so rebuild from those. A chat
        # that was continued after its file was pruned stays on this path:
        # its new file holds only the turns since, the database holds all.
        turns = _chat_turns(chat_id)
        return {"session_id": chat_id, "events": [attach_child_run(e) for e in events_from_runs(turns)]}
    events: list[dict[str, Any]] = []
    for entry in parse_transcript(path):
        events.extend(events_from_transcript_entry(entry))
    return {"session_id": chat_id, "events": [attach_child_run(e) for e in events]}


@router.post("/chat/stream")
async def chat_stream(
    prompt: Annotated[str, Form()],
    session_id: Annotated[str | None, Form()] = None,
    chat_agent: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
):
    """Stream an agent reply as SSE using the unified event schema.

    Routing precedence: `@agent` mention > the chat's locked agent > orchestrator.
    A chat's project is authoritative once it exists; otherwise the form param,
    else Inbox. The project's instructions are layered onto the system prompt.
    """
    sid = session_id or None
    mentioned_agent, actual_prompt = parse_agent_prefix(prompt)
    locked_agent = chat_agent if chat_agent in {a.name for a in list_agents()} else None
    existing_chat = get_chat(sid) if sid else None
    if existing_chat is not None:
        locked_agent = existing_chat.agent
    delegated = False
    if mentioned_agent != "orchestrator":
        orchestrator = get_agent("orchestrator")
        if (locked_agent in (None, "orchestrator")) and mentioned_agent in orchestrator.subagents:
            # An @mention in an orchestrator chat is a one-off delegation
            # inside this conversation — not a fork into a new chat.
            target_agent = "orchestrator"
            delegated = True
        else:
            target_agent = mentioned_agent
            sid = None  # a chat is locked to one agent — switching starts a new one
    elif locked_agent:
        target_agent = locked_agent
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
    append_system = build_project_context(project_record) if project_record else None
    if delegated:
        note = delegation_prompt(mentioned_agent)
        append_system = (append_system + "\n\n" + note) if append_system else note

    # Resuming needs the SDK's transcript file. When Claude Code has pruned it
    # (it keeps sessions ~30 days) `--resume` exits with "No conversation
    # found" — so start a FRESH SDK session under the same id (the chat keeps
    # its identity, links and history) and seed it with what bran still holds.
    resume_sid = sid
    fresh_sid: str | None = None
    if sid and existing_chat is not None and find_session_file(sid) is None:
        resume_sid = None
        fresh_sid = sid
        restored = restored_context(events_from_runs(_chat_turns(sid)))
        if restored:
            append_system = (append_system + "\n\n" + restored) if append_system else restored
        set_setting(DB_HISTORY_KEY + sid, "1")

    # Per-chat model override (the composer's picker). An alias or a full id;
    # anything else is ignored rather than handed to the CLI.
    model_override = (model or "").strip()
    if model_override and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", model_override):
        model_override = ""

    first_prompt = actual_prompt.strip()
    title_seed = (first_prompt[:80].rstrip() + ("…" if len(first_prompt) > 80 else "")) or "(new chat)"

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, default=str)}\n\n"

    async def event_gen():
        chat_initialised = False
        if mentioned_agent != "orchestrator":
            yield sse({"type": "routed", "agent": mentioned_agent if delegated else target_agent})
        # Resuming a session must be exclusive (see background.session_lock):
        # a headless synthesis turn for this chat may be running right now.
        lock = session_lock(sid) if sid else None
        if lock is not None:
            await lock.acquire()
        try:
            async for msg in stream_agent(
                target_agent, actual_prompt, resume_session=resume_sid, session_id=fresh_sid,
                append_system=append_system, project_id=target_project_id,
                model=model_override or None,
            ):
                # Inside-subagent messages belong to the child run (see
                # runner._child_run_for) — the chat shows the Agent tool call
                # and its result, not the subagent's inner monologue.
                if getattr(msg, "parent_tool_use_id", None):
                    continue
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
                    yield sse(attach_child_run(ev))
        except Exception as exc:  # noqa: BLE001 — surface any failure to the client
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            if lock is not None and lock.locked():
                lock.release()
        yield sse({"type": "done"})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
