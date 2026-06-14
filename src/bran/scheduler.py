"""APScheduler integration — runs persisted cron schedules inside `bran serve`.

The scheduler is started/stopped by the FastAPI lifespan. Each schedule row
becomes one CronTrigger; when it fires, we kick off `run_agent()` on the
running event loop (no subprocess), so background runs share the same DB.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bran.config import SETTINGS
from bran.persistence import ScheduleRecord, list_schedules
from bran.runner import run_agent

log = logging.getLogger("bran.scheduler")

_scheduler: AsyncIOScheduler | None = None

# Backoff for retrying a failed scheduled run: 1 min, then 5, then 15 (the last
# delay repeats if BRAN_RUNNER_RETRIES is raised above 3). Transient failures —
# a flaky feed, a network blip — shouldn't cost a whole day's briefing.
_RETRY_DELAYS_S = (60, 300, 900)


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
    from bran.project_files import files_prompt, workdir_prompt

    files = files_prompt(project_id)
    if files:
        parts.append(files)
    workdir = workdir_prompt(project.work_dir)
    if workdir:
        parts.append(workdir)
    return "\n\n".join(parts) if parts else None


def _delta_append_system(schedule_id: str | None) -> str | None:
    """Delta mode: hand the agent its previous completed report so this run
    reports what's NEW/CHANGED instead of restating state. None when there is
    no prior completed run (first fire reports normally)."""
    if not schedule_id:
        return None
    from bran.persistence import list_runs

    prior = list_runs(schedule_id=schedule_id, status="completed", limit=1)
    if not prior or not (prior[0].result or "").strip():
        return None
    prev = prior[0].result.strip()
    if len(prev) > 6000:
        prev = prev[:6000] + "\n[… previous report truncated …]"
    return (
        "## Delta mode — previous report\n"
        f"Your previous scheduled run (ended {prior[0].ended_at}) produced the "
        "report below. Focus this run on what is NEW or CHANGED since then: lead "
        "with the deltas, dedupe anything already covered, and don't restate "
        "unchanged items beyond a brief note. If nothing meaningful has changed, "
        "say so in one short paragraph instead of regenerating the full report.\n\n"
        "<previous_report>\n" + prev + "\n</previous_report>"
    )


def _verification_prompt(task: str, output: str) -> str:
    if len(output) > 12000:
        output = output[:12000] + "\n[… output truncated for review …]"
    return (
        "Review the agent output below against the task it was given.\n\n"
        f"<task>\n{task}\n</task>\n\n<output>\n{output}\n</output>\n\n"
        "Judge ONLY: did the output actually do what the task asked (structure, "
        "scope, completeness), and is it internally consistent (no obvious "
        "fabrication, no placeholder/error text passed off as content)? Style "
        "preferences are not failures. Reply with EXACTLY one JSON object, no "
        "other text:\n"
        '{"pass": true|false, "issues": ["<each concrete problem>"], '
        '"feedback": "<one short paragraph for the agent to fix the issues>"}'
    )


async def _verify_output(task: str, output: str) -> dict | None:
    """Evaluator step (cookbook evaluator-optimizer): a cheap, tool-less query
    judges the output against the task. Returns the parsed verdict dict, or
    None if the critic itself failed — verification fails OPEN (a broken critic
    must never block delivery of a good report)."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a strict but fair reviewer of agent-produced reports. "
            "You only ever reply with a single JSON verdict object."
        ),
        allowed_tools=[],
        model="haiku",
        max_turns=1,
    )
    async def _collect() -> str:
        out = ""
        async for msg in query(prompt=_verification_prompt(task, output), options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        out += block.text
        return out

    try:
        # wait_for (not asyncio.timeout) — bran supports Python 3.10.
        text = await asyncio.wait_for(_collect(), timeout=180)
    except Exception:
        log.exception("verification critic failed")
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        verdict = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(verdict, dict) or "pass" not in verdict:
        return None
    return {
        "pass": bool(verdict.get("pass")),
        "issues": [str(i) for i in (verdict.get("issues") or [])][:10],
        "feedback": str(verdict.get("feedback") or ""),
    }


async def _run_verification(
    record, agent: str, task: str, project_id: str | None,
    schedule_id: str | None, append_system: str | None,
) -> None:
    """Verify a completed scheduled run; on a failed verdict, re-run ONCE with
    the reviewer's feedback and verify that attempt too. Verdicts are stored in
    each run's metadata['verification'] (shown on the run detail page). Never
    raises — verification is a quality layer, not a failure mode."""
    from bran.persistence import update_run

    try:
        verdict = await _verify_output(task, record.result or "")
        if verdict is None:
            record.metadata["verification"] = {"status": "skipped", "reason": "critic failed"}
            update_run(record)
            return
        record.metadata["verification"] = {"status": "done", **verdict}
        if verdict["pass"]:
            update_run(record)
            return

        # Optimizer step: one corrective attempt with the reviewer's feedback.
        log.info("verification failed for %s — re-running once with feedback", record.id)
        feedback = verdict["feedback"] or "; ".join(verdict["issues"]) or "(no detail)"
        fix_suffix = (
            "## Reviewer feedback on your previous attempt\n"
            "A reviewer found problems with the report you produced for this "
            f"exact task:\n{feedback}\n"
            + ("".join(f"- {i}\n" for i in verdict["issues"]))
            + "Produce the report again, fixing these issues."
        )
        retry = await run_agent(
            agent, task, project_id=project_id, source="runner",
            schedule_id=schedule_id,
            append_system=(append_system + "\n\n" + fix_suffix) if append_system else fix_suffix,
        )
        record.metadata["verification"]["retried_run_id"] = retry.id
        update_run(record)
        if retry.status == "completed":
            second = await _verify_output(task, retry.result or "")
            retry.metadata["verification"] = (
                {"status": "done", **second, "retry_of": record.id}
                if second is not None
                else {"status": "skipped", "reason": "critic failed", "retry_of": record.id}
            )
            update_run(retry)
    except Exception:
        log.exception("verification pass failed for run %s", record.id)


async def _fire(
    agent: str, task: str, schedule_name: str, project_id: str | None,
    schedule_id: str | None = None, once: bool = False, attempt: int = 0,
) -> None:
    from bran.persistence import get_schedule

    # Retries fire minutes after the failure — re-check the runner still exists
    # and is enabled (the user may have paused or deleted it in between).
    rec = get_schedule(schedule_name)
    if attempt > 0 and (rec is None or not rec.enabled):
        log.info("retry %d for %s skipped — runner gone or paused", attempt, schedule_name)
        return

    log.info(
        "scheduler firing: %s (%s)%s", schedule_name, agent,
        f" [retry {attempt}/{SETTINGS.runner_retries}]" if attempt else "",
    )
    append_parts = [
        p for p in (
            _project_append_system(project_id),
            _delta_append_system(schedule_id) if (rec is not None and rec.delta) else None,
        ) if p
    ]
    append_system = "\n\n".join(append_parts) if append_parts else None

    failed = False
    try:
        record = await run_agent(
            agent, task, project_id=project_id, source="runner",
            schedule_id=schedule_id, append_system=append_system,
        )
        failed = record.status != "completed"
        if not failed and rec is not None and rec.verify:
            await _run_verification(
                record, agent, task, project_id, schedule_id, append_system
            )
    except asyncio.CancelledError:
        raise  # server shutting down — don't retry, don't touch the schedule
    except Exception:
        log.exception("schedule %s failed", schedule_name)
        failed = True

    retry_scheduled = False
    if failed and attempt < SETTINGS.runner_retries:
        retry_scheduled = _schedule_retry(
            agent, task, schedule_name, project_id, schedule_id, once, attempt + 1
        )
    if failed and not retry_scheduled:
        log.error(
            "schedule %s failed permanently after %d attempt(s)", schedule_name, attempt + 1
        )
    if once and not retry_scheduled:
        # One-shot resolved (success, or retries exhausted) — disable it so it
        # doesn't re-register on restart.
        try:
            from bran.persistence import set_schedule_enabled

            set_schedule_enabled(schedule_name, False)
        except Exception:
            log.exception("failed to disable one-shot %s", schedule_name)
        unregister_schedule(schedule_name)


def _schedule_retry(
    agent: str, task: str, schedule_name: str, project_id: str | None,
    schedule_id: str | None, once: bool, attempt: int,
) -> bool:
    """Queue a one-shot retry of a failed scheduled run with backoff. Returns
    False (no retry) when the live scheduler isn't running."""
    if _scheduler is None:
        return False
    from datetime import datetime, timedelta, timezone

    from apscheduler.triggers.date import DateTrigger

    delay = _RETRY_DELAYS_S[min(attempt - 1, len(_RETRY_DELAYS_S) - 1)]
    when = datetime.now(timezone.utc) + timedelta(seconds=delay)
    try:
        _scheduler.add_job(
            _fire,
            trigger=DateTrigger(run_date=when),
            args=(agent, task, schedule_name, project_id, schedule_id, once, attempt),
            id=f"bran-retry:{schedule_name}:{attempt}",
            name=f"{schedule_name} (retry {attempt})",
            replace_existing=True,
            misfire_grace_time=300,
        )
    except Exception:
        log.exception("could not schedule retry for %s", schedule_name)
        return False
    log.warning(
        "schedule %s failed — retry %d/%d in %ds",
        schedule_name, attempt, SETTINGS.runner_retries, delay,
    )
    return True


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
