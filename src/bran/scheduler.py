"""APScheduler integration — runs persisted cron schedules inside `bran serve`.

The scheduler is started/stopped by the FastAPI lifespan. Each schedule row
becomes one CronTrigger; when it fires, we kick off `run_agent()` on the
running event loop (no subprocess), so background runs share the same DB.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bran.persistence import ScheduleRecord, list_schedules
from bran.runner import run_agent

log = logging.getLogger("bran.scheduler")

_scheduler: AsyncIOScheduler | None = None


def _trigger_from_cron(expr: str) -> CronTrigger:
    """Parse a standard 5-field cron string (minute hour dom month dow)."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(
            f"Invalid cron expression {expr!r}: expected 5 fields, got {len(parts)}"
        )
    minute, hour, dom, month, dow = parts
    return CronTrigger(
        minute=minute, hour=hour, day=dom, month=month, day_of_week=dow
    )


def _project_append_system(project_id: str | None) -> str | None:
    """If a Runner is attached to a project, return its memory as a system-prompt
    suffix so the scheduled run executes *with* the project's context (not just
    tagged to it). None for standalone Runners or projects with no instructions.
    """
    if not project_id:
        return None
    from bran.persistence import get_project

    project = get_project(project_id)
    if project is None:
        return None
    body = (project.instructions or "").strip()
    return f"## Project memory\n{body}" if body else None


async def _fire(agent: str, task: str, schedule_name: str, project_id: str | None) -> None:
    log.info("scheduler firing: %s (%s)", schedule_name, agent)
    try:
        await run_agent(
            agent, task, project_id=project_id,
            append_system=_project_append_system(project_id),
        )
    except Exception:
        log.exception("schedule %s failed", schedule_name)


def start_scheduler() -> None:
    """Spin up APScheduler and register all persisted schedules."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler()
    for rec in list_schedules():
        if rec.enabled:
            _add_job(_scheduler, rec)
    _scheduler.start()
    log.info("scheduler started with %d jobs", len(_scheduler.get_jobs()))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None


def register_schedule(rec: ScheduleRecord) -> None:
    """Register a schedule that was added at runtime."""
    if _scheduler is None or not rec.enabled:
        return
    _add_job(_scheduler, rec)


def unregister_schedule(name: str) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(_job_id(name))
    except Exception:
        pass


def _add_job(scheduler: AsyncIOScheduler, rec: ScheduleRecord) -> None:
    try:
        trigger = _trigger_from_cron(rec.cron)
    except ValueError:
        log.exception("skipping schedule %s — invalid cron", rec.name)
        return
    scheduler.add_job(
        _fire,
        trigger=trigger,
        args=(rec.agent, rec.task, rec.name, rec.project_id),
        id=_job_id(rec.name),
        name=rec.name,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )


def _job_id(name: str) -> str:
    return f"bran-schedule:{name}"
