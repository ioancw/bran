"""Helpers shared by the SPA routers."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import HTTPException, UploadFile, status

from bran.background import spawn_background
from bran.errors import summarize_failure
from bran.persistence import (
    ProjectRecord,
    RunRecord,
    get_project,
    get_run,
    insert_run,
    update_run,
    utcnow_iso,
)
from bran.runner import run_agent

log = logging.getLogger("bran.web.spa")

LIVE = ("running", "pending")
DELEGATION_TOOLS = ("Agent", "Task", "Workflow")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # per file
MAX_UPLOAD_FILES = 20  # per request


def form_flag(value: str | None) -> bool:
    """A checkbox-ish form value: 1/true/yes/on (any case) is True."""
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def run_dict(r: RunRecord) -> dict[str, Any]:
    """asdict(run) plus `error_summary` — the one-sentence human version of
    a failure (None for runs that didn't fail)."""
    d = asdict(r)
    summary = summarize_failure(r.error, r.result) if r.status in ("failed", "cancelled") else None
    d["error_summary"] = summary.as_dict() if summary else None
    return d


def run_or_404(run_id: str) -> RunRecord:
    rec = get_run(run_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
    return rec


def project_or_404(project_id: str) -> ProjectRecord:
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project {project_id}")
    return project


def attach_child_run(ev: dict[str, Any]) -> dict[str, Any]:
    """Give an Agent-tool call event the id of the child run bran recorded
    for it, so the chat can link the delegation to its run."""
    if ev.get("type") == "tool_use" and ev.get("name") in DELEGATION_TOOLS and ev.get("tool_id"):
        from bran.persistence import find_subagent_run

        child = find_subagent_run(str(ev["tool_id"]))
        if child is not None:
            ev = {**ev, "run_id": child.id}
    return ev


def launch_run(
    agent: str, task: str, *, project_id: str | None = None,
    schedule_id: str | None = None, source: str = "manual",
) -> RunRecord:
    """Start a background run and return its (pending) record at once.
    Must be called on the event loop."""
    record = RunRecord.new(
        agent=agent, task=task, project_id=project_id, schedule_id=schedule_id, source=source,
    )
    insert_run(record)

    async def _go() -> None:
        try:
            await run_agent(agent, task, record=record)
        except Exception as exc:  # noqa: BLE001
            # The runner records failures itself once it is driving the run;
            # this is for an error raised BEFORE that (bad options, an agent
            # file that no longer loads), which would otherwise leave the row
            # 'pending' until the next restart.
            if record.status in LIVE:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                record.ended_at = utcnow_iso()
                update_run(record)
            else:
                log.debug("background run %s ended with %r", record.id, exc)

    spawn_background(_go(), name=f"spa-run:{record.id}")
    return record


async def read_capped(f: UploadFile) -> bytes:
    """The upload's bytes, refusing (413) as soon as it passes the per-file
    cap instead of after buffering all of it."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"{f.filename!r} exceeds the {MAX_UPLOAD_BYTES:,} byte limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def check_upload_count(files: list[UploadFile]) -> None:
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            413,
            f"At most {MAX_UPLOAD_FILES} files per upload.",
        )
