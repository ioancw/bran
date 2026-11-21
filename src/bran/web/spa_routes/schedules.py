"""Runners (schedules): list, draft, create, edit, pause/resume, delete."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Request, status

from bran.persistence import ScheduleRecord, list_schedules, set_schedule_enabled
from bran.schedule_service import (
    ScheduleError,
    create_schedule,
    remove_job,
    resolve_trigger,
    sync_job,
    validate_agent,
)
from bran.schedule_service import remove_schedule as remove_runner
from bran.web.spa_routes.common import form_flag, log

router = APIRouter()

_STATUS = {
    "invalid": status.HTTP_400_BAD_REQUEST,
    "conflict": status.HTTP_409_CONFLICT,
    "not_found": status.HTTP_404_NOT_FOUND,
}


def http_error(e: ScheduleError) -> HTTPException:
    return HTTPException(_STATUS.get(e.kind, status.HTTP_400_BAD_REQUEST), str(e))


def schedule_dict(s: ScheduleRecord) -> dict[str, Any]:
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
            except Exception:  # noqa: BLE001 — a bad stored cron simply has no next run
                next_run = None
    d["next_run"] = next_run
    # The last time it should have run and did not (the computer was off or
    # asleep), until it next runs.
    try:
        from bran.scheduler import missed_slot

        d["missed_at"] = missed_slot(s)
    except Exception:  # noqa: BLE001 — a bad stored cron simply has nothing missed
        d["missed_at"] = None
    return d


@router.get("/schedules/parse")
def parse_schedule(expr: str) -> dict[str, Any]:
    """Interpret a schedule string (natural language or cron) without saving —
    powers the live 'this is when it'll run' preview in the schedule forms."""
    from bran.nl_cron import NlCronParseError, parse

    try:
        r = parse(expr)
        return {"ok": True, "cron": r.cron, "human": r.human, "error": ""}
    except NlCronParseError as e:
        return {"ok": False, "cron": "", "human": "", "error": str(e)}


@router.post("/schedules/draft")
async def draft_schedule(text: Annotated[str, Form()]) -> dict[str, Any]:
    """One sentence → a filled-in runner form for the user to confirm."""
    from bran.runner_draft import draft_runner

    text = text.strip()
    if len(text) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Say what the runner should do and when.")
    try:
        return await draft_runner(text)
    except Exception as exc:  # noqa: BLE001 — surfaced as a form error
        log.exception("runner draft failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not draft a runner: {exc}")


@router.get("/schedules")
def schedules(project_id: str | None = None) -> list[dict[str, Any]]:
    return [schedule_dict(s) for s in list_schedules(project_id=project_id or None)]


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
    alert: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    try:
        rec = create_schedule(
            name=name, agent=agent, task=task, cron=cron, run_at=run_at,
            project_id=project_id or None, verify=form_flag(verify),
            delta=form_flag(delta), alert=alert,
        )
    except ScheduleError as e:
        raise http_error(e)
    return schedule_dict(rec)


@router.post("/schedules/{name}")
async def edit_schedule(
    request: Request,
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
        agent = validate_agent(agent)
        cron, run_at = resolve_trigger(cron, run_at)
    except ScheduleError as e:
        raise http_error(e)
    # `alert` comes from the raw form, not a Form() param: FastAPI swallows an
    # EMPTY form value for a defaulted field, which would make "clear the bar"
    # (alert=) indistinguishable from "field absent" (leave unchanged).
    raw_alert = (await request.form()).get("alert")
    rec = update_schedule(
        name, agent=agent, task=task, cron=cron, run_at=run_at,
        # None = field absent from the form = leave unchanged.
        verify=form_flag(verify) if verify is not None else None,
        delta=form_flag(delta) if delta is not None else None,
        alert=str(raw_alert).strip() if raw_alert is not None else None,
    )
    if rec is None:  # deleted between the check and the update
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No schedule {name}")
    sync_job(rec)  # a paused runner stays unregistered
    return schedule_dict(rec)


@router.post("/schedules/{name}/enabled")
async def set_enabled(
    name: str, enabled: Annotated[str, Form()],
) -> dict[str, Any]:
    """Pause/resume a runner and keep the live scheduler in sync."""
    on = form_flag(enabled)
    rec = set_schedule_enabled(name, on)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No schedule {name}")
    if on:
        sync_job(rec)
    else:
        remove_job(name)
    return schedule_dict(rec)


@router.delete("/schedules/{name}")
async def remove_schedule(name: str) -> dict[str, Any]:
    if not remove_runner(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No schedule {name}")
    return {"deleted": name}
