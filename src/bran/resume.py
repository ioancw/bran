"""Background runs that a restart cut off are picked up again.

Every run is an asyncio task inside the server process, so when the process
stops (a crash, a deploy, a reboot) whatever was running stops with it. Until
now each such run was simply marked failed at the next start. For background
work that was wrong twice over: the user had asked for something and got
nothing, and found out only by looking.

At startup (`recover`), each run still marked pending or running is one of:

- **resumed**: a background run the user or an agent started (a spawn, a run
  started by hand). It goes back to pending and is started again. If it had
  got as far as opening its SDK session, that session is resumed and the
  agent is told it was interrupted, so it carries on instead of starting
  over; otherwise it starts from the top.
- **fired again**: a scheduled automation's run. It is marked failed and its
  automation is fired afresh through the scheduler, so the usual context,
  review and notification all apply (unless the scheduler is already about
  to catch that automation up).
- **failed**, as before: a chat turn (the user is there and will ask again),
  an inline step of a turn that no longer exists, anything older than
  `MAX_AGE_HOURS`, and anything that has already been resumed once: a run
  that takes the server down twice is not given a third go.

What this is not: runs still execute in the server's process. Moving them
into separate worker processes would let work survive a restart without
interruption, but bran is one always-on service for one user and that is a
different architecture; this closes the practical gap, which is losing the
work. Set BRAN_RESUME=0 to mark everything failed as before.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from bran.persistence import RunRecord, list_runs, update_run, utcnow_iso

log = logging.getLogger("bran.resume")

RESUMABLE_SOURCES = ("spawn", "manual")
MAX_AGE_HOURS = 6

INTERRUPTED = "Interrupted — the server restarted before this run finished."
REFIRED = "Interrupted — the server restarted before this run finished. Its automation was started again."

_CONTINUE = (
    "(From bran) The server restarted while you were working on this, and the run has been picked "
    "up again. Carry on from where you stopped. Before redoing a step, check what you already "
    "produced (files you wrote, datasets you stored) so nothing is done twice. Finish with the "
    "complete result, as if there had been no interruption. The task was:\n\n"
)


@dataclass
class Recovery:
    resume: list[RunRecord] = field(default_factory=list)
    refire: list[str] = field(default_factory=list)  # schedule ids
    failed: int = 0


def enabled() -> bool:
    return os.getenv("BRAN_RESUME", "1") not in {"0", "false", "no"}


def _fail(record: RunRecord, message: str) -> None:
    record.status = "failed"
    record.error = record.error or message
    record.ended_at = record.ended_at or utcnow_iso()
    update_run(record)


def _recent(record: RunRecord, now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(record.started_at) <= timedelta(hours=MAX_AGE_HOURS)
    except (TypeError, ValueError):
        return False


def recover(now: datetime | None = None) -> Recovery:
    """Decide what happens to every run a restart left in flight, and record
    it. Synchronous and safe to call before the event loop does anything;
    `start` then does the work."""
    from bran.agents import get_agent

    now = now or datetime.now(timezone.utc)
    out = Recovery()
    stranded = [
        r for status in ("pending", "running")
        for r in list_runs(status=status, limit=500)
    ]
    for record in stranded:
        if record.source == "runner" and record.schedule_id and enabled() and _recent(record, now):
            _fail(record, REFIRED)
            if record.schedule_id not in out.refire:
                out.refire.append(record.schedule_id)
            out.failed += 1
            continue
        resumable = (
            enabled()
            and record.source in RESUMABLE_SOURCES
            and not record.metadata.get("inline")
            and not record.metadata.get("resumed")
            and _recent(record, now)
        )
        if resumable:
            try:
                get_agent(record.agent)
            except KeyError:
                resumable = False
        if not resumable:
            _fail(record, INTERRUPTED)
            out.failed += 1
            continue
        record.status = "pending"
        record.metadata["resumed"] = 1
        record.metadata["interrupted_at"] = utcnow_iso()
        update_run(record)
        out.resume.append(record)
    if stranded:
        log.info(
            "after restart: %d run(s) resumed, %d automation(s) to fire again, %d marked failed",
            len(out.resume), len(out.refire), out.failed,
        )
    return out


def _session_to_resume(record: RunRecord) -> str | None:
    """The run's SDK session, if it got far enough to have one on disk."""
    if not record.session_id:
        return None
    from bran.transcript import find_session_file

    return record.session_id if find_session_file(record.session_id) is not None else None


async def _carry_on(record: RunRecord) -> None:
    from bran.runner import run_agent

    session = _session_to_resume(record)
    prompt = (_CONTINUE + record.task) if session else record.task
    try:
        await run_agent(record.agent, prompt, record=record, resume_session=session)
    except Exception as exc:  # noqa: BLE001 — the runner records what it was driving
        if record.status in ("pending", "running"):
            _fail(record, f"{type(exc).__name__}: {exc}")
        log.warning("resumed run %s did not finish: %r", record.id, exc)


def start(recovery: Recovery) -> dict[str, Any]:
    """Start what `recover` decided on. Must be called on the event loop,
    after the scheduler (if any) has started."""
    from bran.background import spawn_background

    for record in recovery.resume:
        spawn_background(_carry_on(record), name=f"resume:{record.id}")
    fired = 0
    if recovery.refire:
        try:
            from bran.scheduler import fire_again

            fired = fire_again(recovery.refire)
        except Exception:
            log.exception("could not fire the interrupted automations again")
    return {"resumed": len(recovery.resume), "fired_again": fired, "failed": recovery.failed}
