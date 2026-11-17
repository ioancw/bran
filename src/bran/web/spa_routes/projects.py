"""Projects: the workspace hub, its memory, its summary and its files — plus
chat-composer attachments."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from bran.persistence import (
    ProjectRecord,
    add_project_memory,
    count_chats_per_project,
    delete_project_memory,
    insert_project,
    list_chats,
    list_project_memories,
    list_projects,
    list_runs,
    list_schedules,
    update_project,
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
from bran.web.spa_routes.common import (
    check_upload_count,
    form_flag,
    launch_run,
    project_or_404,
    read_capped,
)
from bran.web.spa_routes.security import untrusted_file_headers

router = APIRouter()


def build_project_context(project: ProjectRecord) -> str:
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
    summary = (project.summary or "").strip()
    if summary:
        when = (project.summary_at or "")[:10]
        parts.append(f"## Project summary (auto-written{' ' + when if when else ''})\n" + summary)
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


def _project_dict(p: ProjectRecord, n_chats: int) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "instructions": p.instructions, "work_dir": p.work_dir,
        "summary": p.summary, "summary_at": p.summary_at,
        "n_chats": n_chats,
        "updated_at": p.updated_at,
    }


@router.get("/projects")
def projects() -> list[dict[str, Any]]:
    counts = count_chats_per_project()
    return [_project_dict(p, counts.get(p.id, 0)) for p in list_projects()]


@router.get("/projects/{project_id}")
def project_detail(project_id: str) -> dict[str, Any]:
    """The workspace hub: a project plus everything that flows through it —
    chats, schedules, and recent runs (the project activity)."""
    project = project_or_404(project_id)
    from bran.project_summary import find_summary_schedule

    chats = list_chats(limit=200, project_id=project_id)
    daily = find_summary_schedule(project_id)
    return {
        "project": _project_dict(project, len(chats)),
        "n_archived": len(list_chats(limit=1000, project_id=project_id, archived=True)),
        "daily_summary": daily.name if daily else None,
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
    daily_summary: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name is required")
    record = ProjectRecord.new(name=name, description=description.strip())
    insert_project(record)
    if form_flag(daily_summary):
        from bran.project_summary import enable_daily_summary

        enable_daily_summary(record)
    return _project_dict(record, 0)


@router.post("/projects/{project_id}/summarise")
async def summarise_project(project_id: str) -> dict[str, Any]:
    """Refresh the project's summary now: one summariser run that reads the
    project's recent activity and saves a new brief. Returns the run."""
    from bran.project_summary import SUMMARY_AGENT, summary_task

    project_or_404(project_id)
    record = launch_run(SUMMARY_AGENT, summary_task(project_id), project_id=project_id)
    return asdict(record)


@router.post("/projects/{project_id}/daily-summary")
async def set_daily_summary(project_id: str, enabled: Annotated[str, Form()]) -> dict[str, Any]:
    """Turn the daily summary runner on or off for a project."""
    from bran.project_summary import disable_daily_summary, enable_daily_summary

    project = project_or_404(project_id)
    if form_flag(enabled):
        rec = enable_daily_summary(project)
        return {"daily_summary": rec.name}
    disable_daily_summary(project_id)
    return {"daily_summary": None}


@router.post("/projects/{project_id}")
def save_project(
    project_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    instructions: Annotated[str, Form()] = "",
    work_dir: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    project = project_or_404(project_id)
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
    """Delete a project. Its chats, runs and runners are kept and detached;
    its daily-summary runner goes with it (see schedule_service)."""
    from bran.schedule_service import remove_project as remove

    if not remove(project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    return {"deleted": project_id}


@router.get("/projects/{project_id}/memory")
def project_memory(project_id: str) -> list[dict[str, Any]]:
    project_or_404(project_id)
    return [asdict(m) for m in list_project_memories(project_id)]


@router.post("/projects/{project_id}/memory")
def add_memory(project_id: str, text: Annotated[str, Form()]) -> dict[str, Any]:
    """Pin a discrete memory entry to a project (chat 'pin to memory' button +
    the save_project_memory tool's UI equivalent)."""
    project_or_404(project_id)
    text = text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "text required")
    return asdict(add_project_memory(project_id, text))


@router.delete("/projects/{project_id}/memory/{entry_id}")
def remove_memory(project_id: str, entry_id: str) -> dict[str, Any]:
    # The entry must belong to THIS project — the id alone used to be enough.
    if not any(m.id == entry_id for m in list_project_memories(project_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No memory {entry_id}")
    delete_project_memory(entry_id)
    return {"deleted": entry_id}


# ---------------------------------------------------------------------------
# Project working files — managed-upload folder the agents read on demand.
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/files")
def project_files(project_id: str) -> list[dict[str, Any]]:
    project_or_404(project_id)
    return list_project_files(project_id)


@router.post("/projects/{project_id}/files")
async def upload_files(
    project_id: str, files: Annotated[list[UploadFile], File()]
) -> list[dict[str, Any]]:
    """Upload one or more files into the project's working folder."""
    project_or_404(project_id)
    check_upload_count(files)
    saved: list[dict[str, Any]] = []
    for f in files:
        data = await read_capped(f)
        try:
            saved.append(save_project_file(project_id, f.filename or "upload", data))
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return saved


@router.get("/library")
def library() -> list[dict[str, Any]]:
    """Every document across the projects, plus those saved outside one —
    the Artifacts page."""
    from bran.project_files import library as list_library

    return list_library([(p.id, p.name) for p in list_projects()])


@router.post("/uploads")
async def upload_attachments(files: Annotated[list[UploadFile], File()]) -> list[dict[str, Any]]:
    """Chat-composer attachments: one-off files referenced by absolute path in
    the prompt (vs. project files, the per-project library). Saved under
    bran_home/uploads with a unique prefix."""
    check_upload_count(files)
    saved: list[dict[str, Any]] = []
    for f in files:
        data = await read_capped(f)
        try:
            saved.append(save_attachment(f.filename or "upload", data))
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return saved


@router.get("/projects/{project_id}/files/{filename}")
def get_project_file(
    project_id: str, filename: str, download: bool = False
) -> FileResponse:
    """Serve one file from the project's folder.

    HTML documents (from `save_document`) open **inline** so the browser renders
    them for print-to-PDF; `?download=1` forces a save-to-disk instead. The name
    is sanitised to a single component and the resolved path re-checked to sit
    inside the folder (defence in depth over `_safe_name`). Whatever it is, the
    file is untrusted content — see `untrusted_file_headers`.
    """
    project_or_404(project_id)
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
        headers=untrusted_file_headers(p),
    )


@router.delete("/projects/{project_id}/files/{filename}")
def remove_file(project_id: str, filename: str) -> dict[str, Any]:
    project_or_404(project_id)
    try:
        ok = delete_project_file(project_id, filename)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No file {filename!r}")
    return {"deleted": filename}
