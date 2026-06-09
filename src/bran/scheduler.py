"""APScheduler integration — runs persisted cron schedules inside `bran serve`.

The scheduler is started/stopped by the FastAPI lifespan. Each schedule row
becomes one CronTrigger; when it fires, we kick off `run_agent()` on the
running event loop (no subprocess), so background runs share the same DB.
"""

from __future__ import annotations

import asyncio
import logging
import re

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bran.persistence import ScheduleRecord, list_schedules
from bran.runner import run_agent

log = logging.getLogger("bran.scheduler")

_scheduler: AsyncIOScheduler | None = None


# Indexed by *cron* weekday number (0/7=Sunday .. 6=Saturday).
_CRON_DOW_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _translate_dow(dow: str) -> str:
    """Rewrite numeric day-of-week tokens from cron to APScheduler convention.

    Standard cron numbers weekdays 0=Sunday..6=Saturday (7 also Sunday), but
    APScheduler's CronTrigger uses 0=Monday..6=Sunday — a raw `0 9 * * 1`
    passed through verbatim would fire on Tuesday. Day *names* are unambiguous
    (nl_cron emits them for exactly this reason) and pass through unchanged;
    numeric tokens — including ranges, lists, and steps — are expanded to
    explicit name lists so the trigger fires on the days the cron author meant.
    """
    if dow == "*":
        return dow
    out: list[str] = []
    for token in dow.split(","):
        tok = token.strip()
        if not tok:
            continue
        if re.search(r"[a-zA-Z]", tok):
            out.append(tok)
            continue
        body, _, step_s = tok.partition("/")
        step = int(step_s) if step_s else 1
        if step < 1:
            raise ValueError(f"Invalid day-of-week step in {dow!r}")
        if body == "*":
            lo, hi = 0, 6
        elif "-" in body:
            lo_s, hi_s = body.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
        else:
            lo = hi = int(body)
        if not (0 <= lo <= 7 and lo <= hi <= 7):
            raise ValueError(f"Invalid day-of-week value in {dow!r}")
        out.extend(_CRON_DOW_NAMES[d % 7] for d in range(lo, hi + 1, step))
    # dict.fromkeys: dedupe (e.g. "0,7") while keeping order.
    return ",".join(dict.fromkeys(out)) if out else dow


def _trigger_from_cron(expr: str) -> CronTrigger:
    """Parse a standard 5-field cron string (minute hour dom month dow)."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(
            f"Invalid cron expression {expr!r}: expected 5 fields, got {len(parts)}"
        )
    minute, hour, dom, month, dow = parts
    return CronTrigger(
        minute=minute, hour=hour, day=dom, month=month, day_of_week=_translate_dow(dow)
    )


def _project_append_system(project_id: str | None) -> str | None:
    """If a Runner is attached to a project, return its memory as a system-prompt
    suffix so the scheduled run executes *with* the project's context (not just
    tagged to it). None for standalone Runners or projects with no instructions.
    """
    if not project_id:
        return None
    from bran.persistence import get_project, list_project_memories

    project = get_project(project_id)
    if project is None:
        return None
    parts: list[str] = []
    brief = (project.instructions or "").strip()
    if brief:
        parts.append("## Instructions\n" + brief)
    mems = list_project_memories(project_id)
    if mems:
        parts.append("## Memory\n" + "\n".join(f"- {m.text}" for m in mems))
    return "\n\n".join(parts) if parts else None


async def _fire(
    agent: str, task: str, schedule_name: str, project_id: str | None,
    schedule_id: str | None = None, once: bool = False,
) -> None:
    log.info("scheduler firing: %s (%s)", schedule_name, agent)
    try:
        await run_agent(
            agent, task, project_id=project_id, source="runner",
            schedule_id=schedule_id,
            append_system=_project_append_system(project_id),
        )
    except Exception:
        log.exception("schedule %s failed", schedule_name)
    finally:
        if once:
            # One-shot fired — disable it so it doesn't re-register on restart.
            try:
                from bran.persistence import set_schedule_enabled

                set_schedule_enabled(schedule_name, False)
            except Exception:
                log.exception("failed to disable one-shot %s", schedule_name)
            unregister_schedule(schedule_name)


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
    once = bool(rec.run_at)
    if once:
        from datetime import datetime

        from apscheduler.triggers.date import DateTrigger

        try:
            trigger = DateTrigger(run_date=datetime.fromisoformat(rec.run_at))
        except (ValueError, TypeError):
            log.exception("skipping schedule %s — invalid run_at", rec.name)
            return
    else:
        try:
            trigger = _trigger_from_cron(rec.cron)
        except ValueError:
            log.exception("skipping schedule %s — invalid cron", rec.name)
            return
    scheduler.add_job(
        _fire,
        trigger=trigger,
        args=(rec.agent, rec.task, rec.name, rec.project_id, rec.id, once),
        id=_job_id(rec.name),
        name=rec.name,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )


def _job_id(name: str) -> str:
    return f"bran-schedule:{name}"


def next_run_for(cron: str) -> str | None:
    """Next fire time (UTC ISO-8601) for a cron expression, or None if the
    expression is invalid. Read-only — does not touch the running scheduler."""
    from datetime import datetime, timezone

    try:
        trigger = _trigger_from_cron(cron)
    except ValueError:
        return None
    nxt = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
    return nxt.isoformat() if nxt else None
