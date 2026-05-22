"""HTMX + Jinja2 routes for the bran web UI.

Mounted into the FastAPI app by `bran.api.build_app()`. All UI routes go through
`get_current_user()` (currently a no-op anonymous user) so the auth seam is in
place for future OAuth without touching individual handlers.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from bran import __version__
from bran.config import SETTINGS
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
from bran.agents import get_agent, list_agents
from bran.dashboard_data import (
    format_countdown,
    list_outputs,
    today_stats,
    upcoming_schedules,
)
from bran.transcript import (
    find_session_file,
    find_subagent_files,
    parse_transcript,
    summarise_subagents,
)
from bran.runner import stream_agent
from bran.runner import run_agent
from bran.web.auth import WebUser, get_current_user

router = APIRouter(tags=["ui"])

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _nav(active: str, *, runs_count: int | None = None) -> list[dict]:
    """Sidebar items. `active` is one of the page keys."""
    briefings_count = (
        sum(1 for _ in SETTINGS.briefings_dir.glob("*.md"))
        if SETTINGS.briefings_dir.exists() else 0
    )
    return [
        {"href": "/",           "label": "Dashboard", "active": active == "dashboard", "count": None},
        {"href": "/projects",   "label": "Projects",  "active": active == "projects",  "count": len(list_projects())},
        {"href": "/chat",       "label": "Chat",      "active": active == "chat",      "count": None},
        {"href": "/runs",       "label": "Runs",      "active": active == "runs",      "count": runs_count},
        {"href": "/agents",     "label": "Agents",    "active": active == "agents",    "count": len(list_agents())},
        {"href": "/schedules",  "label": "Schedules", "active": active == "schedules", "count": len(list_schedules())},
        {"href": "/briefings",  "label": "Briefings", "active": active == "briefings", "count": briefings_count or None},
    ]


def _ctx(request: Request, user: WebUser, active: str, **extra: Any) -> dict:
    """Common Jinja context. Adds request, user, nav, version, then merges extras."""
    return {
        "request": request,
        "user": user,
        "version": f"v{__version__}",
        "nav": _nav(active),
        **extra,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    upcoming = upcoming_schedules(now=now, limit=3)
    upcoming_with_countdown = [
        {"name": u.name, "agent": u.agent, "cron": u.cron,
         "next_run": u.next_run,
         "countdown": format_countdown(u.next_run, now) if u.next_run else "—"}
        for u in upcoming
    ]
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        _ctx(
            request, user, "dashboard",
            agents=list_agents(),
            stats=today_stats(now=now),
            buckets=list_outputs(limit=60, now=now),
            upcoming=upcoming_with_countdown,
            today_label=now.strftime("%A, %d %B %Y"),
        ),
    )


def _list_slash_commands() -> list[dict[str, str]]:
    """Discover slash commands from `.claude/commands/*.md` for autocomplete.

    Returns a list of {name, description} dicts. The description is parsed
    from the optional YAML frontmatter `description:` field; if absent, falls
    back to the first non-blank prose line.
    """
    out: list[dict[str, str]] = []
    cmd_dir = SETTINGS.claude_dir / "commands"
    if not cmd_dir.exists():
        return out
    for p in sorted(cmd_dir.glob("*.md")):
        name = p.stem
        desc = ""
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        # Quick frontmatter parse — looking for `description: ...`
        m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if m:
            desc = m.group(1).strip().strip("\"'")
        else:
            # First non-blank, non-frontmatter line
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(("---", "name:", "argument-hint:", "allowed-tools:")):
                    desc = line[:120]
                    break
        out.append({"name": name, "description": desc})
    return out


def _build_project_context(project: ProjectRecord) -> str:
    """Assemble the per-chat system-prompt suffix injected for a project chat.

    Two things go in here:
      1. The project's stored instructions (free-form markdown the user wrote).
      2. A short machine-readable header that names the project and tells the
         agent how to pin new memories via the save_project_memory MCP tool.
         The tool needs a project_id, and the agent only has it because we
         inject it here — otherwise it would have to guess.
    """
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
    """Detect `@name <rest>` or `@agent-name <rest>` and route accordingly.

    Returns (agent_name, stripped_prompt). If no recognised agent is named,
    returns ("orchestrator", original_prompt).
    """
    m = re.match(r"^@(?:agent-)?([\w-]+)\s+(.*)", prompt, re.DOTALL)
    if not m:
        return ("orchestrator", prompt)
    candidate = m.group(1)
    known = {a.name for a in list_agents()}
    if candidate in known:
        return (candidate, m.group(2).strip())
    return ("orchestrator", prompt)


def _chat_page_response(
    request: Request,
    user: WebUser,
    active_session_id: str | None,
    project_query: str | None = None,
):
    """Shared rendering for `/chat` and `/chat/{session_id}`.

    Project scoping rules:
    - If an active chat is loaded, its project_id is authoritative — the
      sidebar shows siblings in that project, new chats inherit the project.
    - Otherwise, `?project=<id>` (or None for "all") decides the scope.
    """
    active = get_chat(active_session_id) if active_session_id else None

    if active is not None:
        project_id = active.project_id
    else:
        project_id = project_query  # may be None → global list

    chats = list_chats(limit=100, project_id=project_id)
    if active is None and chats and project_id is not None:
        # Landing on a project page with chats — open the most recent one.
        active = chats[0]

    project = get_project(project_id) if project_id else None

    catalog = {
        "agents": [
            {"name": a.name, "description": a.description}
            for a in list_agents()
        ],
        "commands": _list_slash_commands(),
    }
    chats_payload = [
        {"id": c.id, "title": c.title, "agent": c.agent,
         "project_id": c.project_id,
         "updated_at": c.updated_at, "created_at": c.created_at}
        for c in chats
    ]
    return _templates.TemplateResponse(
        request,
        "chat.html",
        _ctx(
            request, user, "chat",
            catalog_json=json.dumps(catalog),
            chats_json=json.dumps(chats_payload),
            active_chat=active,
            active_chat_json=json.dumps(
                {"id": active.id, "title": active.title, "agent": active.agent,
                 "project_id": active.project_id}
                if active else None
            ),
            project=project,
            project_id=project_id,    # may be None for global scope
            all_projects=list_projects(),
        ),
    )


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    project: str | None = None,
):
    """Chat landing — global list, or project-scoped if ?project=<id> is given."""
    return _chat_page_response(request, user, None, project_query=project)


@router.get("/chat/{session_id}", response_class=HTMLResponse)
async def chat_page_specific(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    session_id: str,
):
    """URL-addressable chat — opens the specific session."""
    return _chat_page_response(request, user, session_id)


@router.get("/api/chats")
async def api_list_chats(
    user: Annotated[WebUser, Depends(get_current_user)],
):
    """Return recent chats as JSON for the sidebar to refresh after a new
    message or after a new chat is created."""
    return [
        {"id": c.id, "title": c.title, "agent": c.agent,
         "updated_at": c.updated_at, "created_at": c.created_at}
        for c in list_chats(limit=100)
    ]


@router.delete("/api/chats/{chat_id}")
async def api_delete_chat(
    user: Annotated[WebUser, Depends(get_current_user)],
    chat_id: str,
):
    """Remove a chat from the metadata table. NB: the underlying SDK session
    JSONL file is NOT deleted — it stays in `~/.claude/projects/...` and can
    still be resumed via `/chat/history/{id}` or the runs detail page."""
    if not delete_chat(chat_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return {"deleted": chat_id}


# ---------------------------------------------------------------------------
# Chat backend: SSE stream + history loader
# ---------------------------------------------------------------------------

def _serialize_block(block: Any) -> dict[str, Any] | None:
    """Convert an SDK content block to a JSON shape the frontend understands."""
    btype = type(block).__name__
    if btype == "TextBlock":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "ThinkingBlock":
        return {"type": "thinking", "text": getattr(block, "thinking", "")}
    if btype == "ToolUseBlock":
        name = getattr(block, "name", "")
        inp = getattr(block, "input", {}) or {}
        if name in ("Agent", "Task"):
            return {
                "type": "delegation",
                "subagent": inp.get("subagent_type"),
                "prompt": inp.get("prompt", ""),
                "tool_id": getattr(block, "id", None),
            }
        return {
            "type": "tool_use",
            "name": name,
            "input": inp,
            "tool_id": getattr(block, "id", None),
        }
    if btype == "ToolResultBlock":
        content = getattr(block, "content", None)
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            text = "\n".join(parts)
        else:
            text = str(content or "")
        return {
            "type": "tool_result",
            "tool_id": getattr(block, "tool_use_id", None),
            "is_error": bool(getattr(block, "is_error", False)),
            "text": text,
        }
    return None


def _serialize_message(message: Any) -> dict[str, Any] | None:
    """Convert one SDK message into one or more frontend events.

    Returns a single event dict; the streamer wraps it as SSE. Returns None
    for messages we don't surface to the chat UI (anything we can't classify).
    """
    mtype = type(message).__name__
    if mtype == "SystemMessage" and getattr(message, "subtype", None) == "init":
        sid = (getattr(message, "data", None) or {}).get("session_id")
        return {"type": "session", "session_id": sid} if sid else None
    if mtype == "AssistantMessage":
        blocks = []
        for b in getattr(message, "content", []) or []:
            serialized = _serialize_block(b)
            if serialized:
                blocks.append(serialized)
        if not blocks:
            return None
        return {"type": "assistant", "blocks": blocks}
    if mtype == "UserMessage":
        # User messages in the stream are typically tool results echoed back.
        blocks = []
        content = getattr(getattr(message, "message", None), "content", None) or getattr(message, "content", None)
        if isinstance(content, list):
            for b in content:
                # The SDK gives us dicts here, not typed blocks
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    raw = b.get("content")
                    if isinstance(raw, list):
                        parts = [c.get("text", "") for c in raw if isinstance(c, dict) and c.get("type") == "text"]
                        text = "\n".join(parts)
                    else:
                        text = str(raw or "")
                    blocks.append({
                        "type": "tool_result",
                        "tool_id": b.get("tool_use_id"),
                        "is_error": bool(b.get("is_error")),
                        "text": text,
                    })
        return {"type": "user_tool_results", "blocks": blocks} if blocks else None
    if mtype == "ResultMessage":
        return {
            "type": "result",
            "session_id": getattr(message, "session_id", None),
            "num_turns": getattr(message, "num_turns", None),
            "total_cost_usd": getattr(message, "total_cost_usd", None),
        }
    return None


@router.post("/chat/stream")
async def chat_stream(
    user: Annotated[WebUser, Depends(get_current_user)],
    prompt: Annotated[str, Form()],
    session_id: Annotated[str | None, Form()] = None,
    chat_agent: Annotated[str | None, Form()] = None,
    project_id: Annotated[str | None, Form()] = None,
):
    """Stream an agent's response to a user prompt as SSE.

    Routing precedence (highest → lowest):
      1. `@<agent> <prompt>` in the prompt itself overrides everything else.
      2. The chat's locked `chat_agent` (passed by the UI) when set.
      3. Falls back to `orchestrator`.

    Project scoping: when a chat lives inside a project (existing or new),
    the project's `instructions` blob is appended to the agent's system
    prompt for every message — that's how project memory layers on top.

    `/<command> [args]` is passed through verbatim — the SDK auto-loads
    .claude/commands/*.md so the slash command resolves itself.
    """
    sid = session_id or None
    mentioned_agent, actual_prompt = _parse_agent_prefix(prompt)
    if mentioned_agent != "orchestrator":
        target_agent = mentioned_agent  # @-mention wins
        sid = None  # don't resume across different agents
    elif chat_agent and chat_agent in {a.name for a in list_agents()}:
        target_agent = chat_agent
    else:
        target_agent = "orchestrator"

    # Resolve the project this chat will be associated with. Existing chat?
    # Trust its stored project_id. Otherwise honour the form param, else Inbox.
    if sid:
        existing_chat = get_chat(sid)
        target_project_id = existing_chat.project_id if existing_chat else (project_id or INBOX_PROJECT_ID)
    else:
        target_project_id = project_id or INBOX_PROJECT_ID

    project_record = get_project(target_project_id)
    if project_record is None:
        # Unknown project id from a stale URL or client bug — drop into Inbox
        # rather than persisting an orphan chat with a dangling project_id.
        target_project_id = INBOX_PROJECT_ID
        project_record = get_project(INBOX_PROJECT_ID)

    append_system = _build_project_context(project_record) if project_record else None

    # Capture for chat-record upsert below
    first_prompt = actual_prompt.strip()
    title_seed = (first_prompt[:80].rstrip() + ("…" if len(first_prompt) > 80 else "")) or "(new chat)"

    async def event_gen():
        chat_initialised = False
        # Surface the routing decision so the UI can show "routed to X"
        if mentioned_agent != "orchestrator":
            yield f"data: {json.dumps({'type': 'routed', 'agent': target_agent})}\n\n"
        try:
            async for msg in stream_agent(
                target_agent, actual_prompt,
                resume_session=sid, append_system=append_system,
            ):
                event = _serialize_message(msg)
                if event is None:
                    continue
                # On the first session event, upsert the chat metadata row.
                # This is what makes a chat appear in the sidebar list.
                if not chat_initialised and event.get("type") == "session":
                    sid_now = event.get("session_id")
                    if sid_now:
                        existing = get_chat(sid_now)
                        if existing is None:
                            upsert_chat(ChatRecord(
                                id=sid_now,
                                title=title_seed,
                                agent=target_agent,
                                project_id=target_project_id,
                            ))
                        else:
                            touch_chat(sid_now)
                        chat_initialised = True
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:
            err = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            yield f"data: {json.dumps(err)}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )


@router.get("/chat/history/{session_id}")
async def chat_history(
    user: Annotated[WebUser, Depends(get_current_user)],
    session_id: str,
):
    """Replay a past session's transcript so the chat page can rehydrate on
    refresh. Reuses the transcript parser used by /runs/{id}/transcript."""
    path = find_session_file(session_id)
    if path is None:
        return {"entries": []}
    entries = parse_transcript(path)
    out = []
    for e in entries:
        # Pre-format into the same event shape the streamer emits, so the
        # frontend can replay them through the same render path.
        if e.kind == "user_text":
            out.append({"kind": "user_text", "text": e.text})
        elif e.kind == "assistant_text":
            out.append({"kind": "assistant_text", "text": e.text})
        elif e.kind == "thinking":
            out.append({"kind": "thinking", "text": e.text})
        elif e.kind == "tool_call":
            out.append({
                "kind": "tool_call",
                "name": e.tool_name,
                "input": e.tool_input,
                "tool_id": e.tool_id,
            })
        elif e.kind == "delegation":
            out.append({
                "kind": "delegation",
                "subagent": e.subagent_type,
                "prompt": (e.tool_input or {}).get("prompt", ""),
                "tool_id": e.tool_id,
            })
        elif e.kind == "tool_result":
            out.append({
                "kind": "tool_result",
                "tool_id": e.tool_id,
                "is_error": bool(e.tool_is_error),
                "text": e.text,
            })
    return {"session_id": session_id, "entries": out}


# ---------------------------------------------------------------------------
# Projects — containers for related chats + memory (Claude-Cowork style)
# ---------------------------------------------------------------------------


@router.get("/projects", response_class=HTMLResponse)
async def projects_page(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
):
    """Grid of project cards + create form."""
    projects = list_projects()
    counts = count_chats_per_project()
    payload = [
        {
            "id": p.id, "name": p.name, "description": p.description,
            "n_chats": counts.get(p.id, 0),
            "updated_at": p.updated_at,
            "is_inbox": p.id == INBOX_PROJECT_ID,
        }
        for p in projects
    ]
    return _templates.TemplateResponse(
        request, "projects.html",
        _ctx(request, user, "projects", projects=payload),
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    project_id: str,
):
    """Project page: header, memory editor, chats list, new-chat affordance."""
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    chats = list_chats(limit=200, project_id=project_id)
    return _templates.TemplateResponse(
        request, "project_detail.html",
        _ctx(
            request, user, "projects",
            project=project,
            chats=chats,
            is_inbox=project_id == INBOX_PROJECT_ID,
        ),
    )


@router.post("/ui/new-project")
async def ui_new_project(
    user: Annotated[WebUser, Depends(get_current_user)],
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
):
    """Create a new project. Redirects to its detail page so the user can
    write instructions and start chatting immediately."""
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is required")
    record = ProjectRecord.new(name=name, description=description.strip())
    insert_project(record)
    return RedirectResponse(f"/projects/{record.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/ui/projects/{project_id}/save", response_class=HTMLResponse)
async def ui_save_project(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    project_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    instructions: Annotated[str, Form()] = "",
):
    """HTMX-savable form for project name/description/instructions.

    Returns a small saved-flash partial so the editor confirms without a
    full page reload — the textarea state is preserved."""
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    project.name = name.strip() or project.name
    project.description = description.strip()
    project.instructions = instructions
    update_project(project)
    # Ephemeral confirmation: visible on swap, fades to nothing after ~2.5s
    # so the editor doesn't look 'permanently saved' even after the user has
    # gone on to make further edits.
    return HTMLResponse(
        '<span class="save-flash" '
        'style="font-family: var(--font-mono); font-size: 10px; '
        'text-transform: uppercase; letter-spacing: 0.14em; '
        'color: var(--accent-soft); transition: opacity 0.6s ease-out;">saved ✓</span>'
        '<script>'
        'setTimeout(() => { '
        ' const el = document.querySelector(\'.save-flash\'); '
        ' if (el) { el.style.opacity = \'0\'; '
        '   setTimeout(() => el.remove(), 700); } '
        '}, 2200);'
        '</script>'
    )


@router.post("/ui/projects/{project_id}/delete")
async def ui_delete_project(
    user: Annotated[WebUser, Depends(get_current_user)],
    project_id: str,
):
    """Remove a project. Chats inside are reassigned to Inbox, not deleted."""
    if project_id == INBOX_PROJECT_ID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Inbox cannot be deleted")
    if not delete_project(project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/projects/{project_id}/memory/append")
async def api_append_project_memory(
    user: Annotated[WebUser, Depends(get_current_user)],
    project_id: str,
    text: Annotated[str, Form()],
):
    """Append a chunk of text to a project's instructions/memory.

    Used by the chat UI's 'save to memory' button (which lifts an assistant
    reply verbatim) and by the save_project_memory MCP tool (whose path
    goes via update_project directly, not through this endpoint)."""
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    text = text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "text required")
    sep = "\n\n" if (project.instructions or "").strip() else ""
    project.instructions = (project.instructions or "") + sep + text
    update_project(project)
    return {
        "ok": True,
        "project_id": project_id,
        "memory_length": len(project.instructions),
    }


@router.post("/ui/chats/{chat_id}/move")
async def ui_move_chat(
    user: Annotated[WebUser, Depends(get_current_user)],
    chat_id: str,
    project_id: Annotated[str, Form()],
):
    """Move a chat to a different project."""
    if get_project(project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    if not move_chat_to_project(chat_id, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No chat {chat_id}")
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    agent: str | None = None,
    status_: str | None = None,
):
    # FastAPI query alias — accept ?status= without colliding with the `status` import
    runs = list_runs(agent=agent or None, status=status_ or None, limit=200)
    return _templates.TemplateResponse(
        request,
        "runs.html",
        _ctx(
            request, user, "runs",
            runs=runs,
            agents=list_agents(),
            agent_filter=agent,
            status_filter=status_,
        ),
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    run_id: str,
):
    record = get_run(run_id)
    if record is None:
        # Allow id prefix for hand-typed URLs
        candidates = [r for r in list_runs(limit=500) if r.id.startswith(run_id)]
        if len(candidates) == 1:
            return RedirectResponse(f"/runs/{candidates[0].id}", status_code=status.HTTP_302_FOUND)
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    return _templates.TemplateResponse(
        request,
        "run_detail.html",
        _ctx(request, user, "runs", run=record),
    )


@router.get("/runs/{run_id}/transcript", response_class=HTMLResponse)
async def run_transcript(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    run_id: str,
):
    """Render the SDK's message-by-message JSONL transcript for a run."""
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")

    entries = []
    subagents = []
    transcript_path = None
    if record.session_id:
        path = find_session_file(record.session_id)
        if path is not None:
            transcript_path = str(path)
            entries = parse_transcript(path)
            subagents = summarise_subagents(record.session_id)

    return _templates.TemplateResponse(
        request,
        "transcript.html",
        _ctx(
            request, user, "runs",
            run=record,
            entries=entries,
            subagents=subagents,
            transcript_path=transcript_path,
            is_subagent=False,
        ),
    )


@router.get("/runs/{run_id}/subagents/{filename}", response_class=HTMLResponse)
async def subagent_transcript(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    run_id: str,
    filename: str,
):
    """Render a single sub-agent transcript belonging to a parent run."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid filename")

    record = get_run(run_id)
    if record is None or not record.session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")

    candidates = [p for p in find_subagent_files(record.session_id) if p.name == filename]
    if not candidates:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No subagent {filename}")
    path = candidates[0]
    entries = parse_transcript(path)

    return _templates.TemplateResponse(
        request,
        "transcript.html",
        _ctx(
            request, user, "runs",
            run=record,
            entries=entries,
            subagents=[],
            transcript_path=str(path),
            is_subagent=True,
            subagent_filename=filename,
        ),
    )


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
):
    return _templates.TemplateResponse(
        request,
        "agents.html",
        _ctx(request, user, "agents", agents=list_agents()),
    )


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_page(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
):
    return _templates.TemplateResponse(
        request,
        "schedules.html",
        _ctx(
            request, user, "schedules",
            schedules=list_schedules(),
            agents=list_agents(),
        ),
    )


@router.get("/briefings", response_class=HTMLResponse)
async def briefings_page(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
):
    """List markdown briefings written to .bran/briefings/ by scheduled or ad-hoc runs."""
    files = sorted(
        SETTINGS.briefings_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    briefings = [
        {
            "name": p.name,
            "size_kb": round(p.stat().st_size / 1024, 1),
            "mtime": p.stat().st_mtime,
        }
        for p in files
    ]
    return _templates.TemplateResponse(
        request,
        "briefings.html",
        _ctx(request, user, "briefings", briefings=briefings),
    )


@router.get("/briefings/{filename}", response_class=HTMLResponse)
async def briefing_detail(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    filename: str,
):
    # Defensive: refuse anything that tries to escape the briefings dir.
    if "/" in filename or "\\" in filename or filename.startswith(".."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid filename")
    path = SETTINGS.briefings_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No briefing {filename}")
    content = path.read_text(encoding="utf-8")
    return _templates.TemplateResponse(
        request,
        "briefing_detail.html",
        _ctx(request, user, "briefings", filename=filename, content=content),
    )


# ---------------------------------------------------------------------------
# HTMX-targeted partials
# ---------------------------------------------------------------------------


@router.post("/ui/new-run", response_class=HTMLResponse)
async def ui_new_run(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    agent: Annotated[str, Form()],
    task: Annotated[str, Form()],
):
    """Launch an agent run in the background, return its row for HTMX to inject."""
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    record = RunRecord.new(agent=agent, task=task)
    insert_run(record)

    async def _go():
        try:
            await run_agent(agent, task, record=record)
        except Exception:
            pass  # runner persists the failure

    asyncio.create_task(_go(), name=f"ui-run:{record.id}")

    return _templates.TemplateResponse(
        request,
        "partials/run_row.html",
        {"run": record},
    )


@router.get("/ui/runs/{run_id}/row", response_class=HTMLResponse)
async def ui_run_row(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    run_id: str,
):
    """Single-row refresh target for HTMX polling on in-flight runs."""
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    return _templates.TemplateResponse(
        request,
        "partials/run_row.html",
        {"run": record},
    )


@router.post("/ui/agents/{name}/launch")
async def ui_agent_launch(
    user: Annotated[WebUser, Depends(get_current_user)],
    name: str,
    task: Annotated[str, Form()],
):
    """Launch an agent run from the agent card and HTMX-redirect to its detail page.

    Returns HX-Redirect rather than a row partial because the agent-card flow
    wants the user to be taken straight to the run they just fired, not have
    a row injected into a table they may not be looking at.
    """
    try:
        get_agent(name)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    record = RunRecord.new(agent=name, task=task)
    insert_run(record)

    async def _go():
        try:
            await run_agent(name, task, record=record)
        except Exception:
            pass

    asyncio.create_task(_go(), name=f"ui-launch:{record.id}")
    return Response(status_code=200, headers={"HX-Redirect": f"/runs/{record.id}"})


@router.post("/ui/runs/{run_id}/rerun")
async def ui_rerun(
    user: Annotated[WebUser, Depends(get_current_user)],
    run_id: str,
):
    """Re-fire an existing run with the same agent + task. Redirects to the new run."""
    original = get_run(run_id)
    if original is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")

    record = RunRecord.new(
        agent=original.agent, task=original.task, parent_run_id=original.id
    )
    insert_run(record)

    async def _go():
        try:
            await run_agent(original.agent, original.task, record=record)
        except Exception:
            pass

    asyncio.create_task(_go(), name=f"ui-rerun:{record.id}")
    return RedirectResponse(f"/runs/{record.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/ui/new-schedule", response_class=HTMLResponse)
async def ui_new_schedule(
    request: Request,
    user: Annotated[WebUser, Depends(get_current_user)],
    name: Annotated[str, Form()],
    agent: Annotated[str, Form()],
    cron: Annotated[str, Form()],
    task: Annotated[str, Form()] = "",
):
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    rec = ScheduleRecord.new(name=name, agent=agent, task=task, cron=cron)
    insert_schedule(rec)
    # Register with the live scheduler if one is running. Imported lazily so the
    # web module doesn't pull APScheduler in when --no-scheduler is used.
    try:
        from bran.scheduler import register_schedule

        register_schedule(rec)
    except Exception:
        pass
    return _templates.TemplateResponse(
        request,
        "partials/schedule_row.html",
        {"s": rec},
    )


@router.delete("/ui/schedules/{name}", response_class=HTMLResponse)
async def ui_delete_schedule(
    user: Annotated[WebUser, Depends(get_current_user)],
    name: str,
):
    if not delete_schedule(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        from bran.scheduler import unregister_schedule

        unregister_schedule(name)
    except Exception:
        pass
    # Return an empty body so HTMX swap removes the row.
    return HTMLResponse("")
