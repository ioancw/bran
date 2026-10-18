"""A project's summary that writes itself.

The summariser agent reads the project's recent activity through
`get_project_activity`, writes a short brief, and stores it with
`save_project_summary`. Two ways to trigger it: the Refresh button on the
project page (one run, now) and an optional daily runner attached to the
project. The summary then rides in every project chat's system prompt, so an
agent arriving cold knows what the project is about and where it stands.
"""

from __future__ import annotations

import re

from bran.persistence import (
    ProjectRecord,
    ScheduleRecord,
    delete_schedule,
    insert_schedule,
    list_schedules,
)

SUMMARY_AGENT = "summariser"
SUMMARY_CRON = "0 18 * * *"  # once a day, early evening server time
_TASK_MARKER = "[project summary]"


def summary_task(project_id: str) -> str:
    return (
        f"{_TASK_MARKER} Refresh the summary of bran project `{project_id}`.\n"
        f"1. Call `mcp__bran__get_project_activity` with project_id=\"{project_id}\".\n"
        "2. Write a summary for an agent arriving cold, in markdown, 150–300 words: "
        "a one-paragraph 'What this project is about', then 'Recent work' (the last "
        "few outcomes, newest first, with dates), then 'Open threads' (unfinished "
        "questions, pending decisions, things worth watching). Keep what still "
        "holds from the previous summary; drop what is stale; never pad.\n"
        f"3. Save it with `mcp__bran__save_project_summary` (project_id=\"{project_id}\").\n"
        "Reply with the summary you saved."
    )


def is_summary_schedule(s: ScheduleRecord) -> bool:
    return s.agent == SUMMARY_AGENT and s.task.startswith(_TASK_MARKER)


def find_summary_schedule(project_id: str) -> ScheduleRecord | None:
    return next((s for s in list_schedules(project_id=project_id) if is_summary_schedule(s)), None)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "project"


def enable_daily_summary(project: ProjectRecord) -> ScheduleRecord:
    """Attach the daily summary runner to a project (idempotent)."""
    existing = find_summary_schedule(project.id)
    if existing is not None:
        return existing
    base = f"{_slug(project.name)}-summary"
    names = {s.name for s in list_schedules()}
    name, n = base, 2
    while name in names:
        name, n = f"{base}-{n}", n + 1
    rec = ScheduleRecord.new(
        name=name, agent=SUMMARY_AGENT, task=summary_task(project.id),
        cron=SUMMARY_CRON, project_id=project.id,
    )
    insert_schedule(rec)
    try:
        from bran.scheduler import register_schedule

        register_schedule(rec)
    except Exception:  # noqa: BLE001 — no live scheduler (tests, --no-scheduler)
        pass
    return rec


def disable_daily_summary(project_id: str) -> bool:
    existing = find_summary_schedule(project_id)
    if existing is None:
        return False
    delete_schedule(existing.name)
    try:
        from bran.scheduler import unregister_schedule

        unregister_schedule(existing.name)
    except Exception:  # noqa: BLE001
        pass
    return True
