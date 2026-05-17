"""HTMX + Jinja2 routes for the bran web UI.

Mounted into the FastAPI app by `bran.api.build_app()`. All UI routes go through
`get_current_user()` (currently a no-op anonymous user) so the auth seam is in
place for future OAuth without touching individual handlers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from bran import __version__
from bran.config import SETTINGS
from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    delete_schedule,
    get_run,
    insert_run,
    insert_schedule,
    list_runs,
    list_schedules,
)
from bran.agents import get_agent, list_agents
from bran.dashboard_data import (
    format_countdown,
    latest_briefing,
    today_stats,
    upcoming_schedules,
)
from bran.transcript import (
    find_session_file,
    find_subagent_files,
    parse_transcript,
    summarise_subagents,
)
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
            briefing=latest_briefing(),
            upcoming=upcoming_with_countdown,
            today_label=now.strftime("%A, %d %B %Y"),
        ),
    )


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
