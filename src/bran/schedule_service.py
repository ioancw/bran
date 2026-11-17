"""One place that creates, re-syncs and removes runners.

Three surfaces make runners — the SPA form, the bearer-token API and the
orchestrator's `create_runner` tool. They used to validate differently (one
answered a duplicate name with a 500, one stored a cron that could never
fire, all three swallowed scheduler errors), so the rules live here once and
each surface only translates `ScheduleError` into its own kind of reply.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime

from bran.persistence import (
    ScheduleRecord,
    delete_project,
    delete_schedule,
    get_schedule,
    insert_schedule,
    list_schedules,
)

log = logging.getLogger("bran.schedules")

# A name is the runner's handle everywhere: the URL path segment, the
# scheduler's job id, what the user types in chat. Anything that cannot
# survive a URL path would make the runner impossible to edit or delete.
_NAME_MAX = 80
_NAME_BAD = re.compile(r"[/\\?#%\x00-\x1f]")


class ScheduleError(ValueError):
    """A runner request that cannot be honoured. `kind` is 'invalid'
    (bad input), 'conflict' (the name is taken) or 'not_found'."""

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ScheduleError("A runner needs a name.")
    if len(name) > _NAME_MAX:
        raise ScheduleError(f"A runner name can be at most {_NAME_MAX} characters.")
    if _NAME_BAD.search(name):
        raise ScheduleError("A runner name cannot contain / \\ ? # or %.")
    return name


def validate_agent(agent: str) -> str:
    from bran.agents import get_agent

    agent = (agent or "").strip()
    try:
        get_agent(agent)
    except KeyError:
        raise ScheduleError(f"Unknown agent {agent!r}.")
    return agent


def resolve_trigger(cron: str, run_at: str | None) -> tuple[str, str | None]:
    """(cron, run_at) as stored. `run_at` (an ISO-8601 datetime) makes a
    one-shot and clears cron; otherwise `cron` is required and may be plain
    English or a 5-field expression — it is normalised, then checked against
    the scheduler itself so nothing is stored that could never fire."""
    run_at = (run_at or "").strip() or None
    if run_at:
        try:
            datetime.fromisoformat(run_at)
        except ValueError:
            raise ScheduleError(f"invalid run_at: {run_at!r} (expected an ISO-8601 datetime)")
        return "", run_at
    if not (cron or "").strip():
        raise ScheduleError("cron or run_at required")
    from bran.nl_cron import NlCronParseError, parse

    try:
        canonical = parse(cron).cron
    except NlCronParseError as e:
        raise ScheduleError(str(e))
    from bran.scheduler import _trigger_from_cron

    try:
        _trigger_from_cron(canonical)
    except ValueError as e:
        raise ScheduleError(f"The scheduler cannot run `{canonical}`: {e}")
    return canonical, None


def create_schedule(
    *, name: str, agent: str, task: str = "", cron: str = "", run_at: str | None = None,
    project_id: str | None = None, verify: bool = False, delta: bool = False,
    alert: str = "", teammate_id: str | None = None,
) -> ScheduleRecord:
    """Validate, store and register a new runner. Raises ScheduleError."""
    name = validate_name(name)
    agent = validate_agent(agent)
    cron, run_at = resolve_trigger(cron, run_at)
    if get_schedule(name) is not None:
        raise ScheduleError(
            f"A runner named {name!r} already exists — pick another name or delete it first.",
            kind="conflict",
        )
    rec = ScheduleRecord.new(
        name=name, agent=agent, task=task, cron=cron, project_id=project_id or None,
        run_at=run_at, verify=verify, delta=delta, alert=(alert or "").strip(),
        teammate_id=teammate_id or None,
    )
    try:
        insert_schedule(rec)
    except sqlite3.IntegrityError:
        # Lost a race with another create of the same name.
        raise ScheduleError(f"A runner named {name!r} already exists.", kind="conflict")
    sync_job(rec)
    return rec


def sync_job(rec: ScheduleRecord) -> None:
    """Make the live scheduler match the stored runner: drop its job, then
    add it back if the runner is enabled. A no-op without a live scheduler
    (CLI, tests, --no-scheduler). A scheduler that refuses the job is logged
    — the runner would otherwise show a next run that never comes."""
    try:
        from bran.scheduler import register_schedule, unregister_schedule

        unregister_schedule(rec.name)
        register_schedule(rec)
    except Exception:
        log.exception("could not sync runner %r with the live scheduler", rec.name)


def remove_job(name: str) -> None:
    try:
        from bran.scheduler import unregister_schedule

        unregister_schedule(name)
    except Exception:
        log.exception("could not remove runner %r from the live scheduler", name)


def remove_schedule(name: str) -> bool:
    """Delete a runner and its live job. False when there was none."""
    if not delete_schedule(name):
        return False
    remove_job(name)
    return True


def remove_project(project_id: str) -> bool:
    """Delete a project and leave nothing running against it.

    Its daily-summary runner only exists to summarise the project, so it is
    deleted. Its other runners are kept but detached — and their live jobs
    re-registered, because a job carries the project id it was created with
    and would otherwise keep running in the deleted project's name. Chats and
    runs are detached; an empty files folder is removed (a folder with files
    in it is left alone — the files are the user's)."""
    from bran.project_files import files_dir
    from bran.project_summary import is_summary_schedule

    attached = list_schedules(project_id=project_id)
    if not delete_project(project_id):
        return False
    for s in attached:
        if is_summary_schedule(s):
            remove_schedule(s.name)
            continue
        fresh = get_schedule(s.name)
        if fresh is not None:
            sync_job(fresh)
    try:
        folder = files_dir(project_id)
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder.parent.rmdir()  # only succeeds when nothing else is in it
    except OSError:
        log.debug("could not tidy the files folder of deleted project %s", project_id)
    return True
