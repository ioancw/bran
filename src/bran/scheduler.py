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
        "unchanged items beyond a brief note. The most valuable signal is the "
        "second derivative — call out anything ACCELERATING (a story growing "
        "since last time, a move extending, coverage spreading across sources) "
        "and distinguish it from one-off news. Close with a line or two on what "
        "the changes imply. If nothing meaningful has changed, say so in one "
        "short paragraph instead of regenerating the full report.\n\n"
        "<previous_report>\n" + prev + "\n</previous_report>"
    )


def _alert_append_system(condition: str) -> str:
    """Alert mode: the runner is a sensing loop with a significance bar. The
    agent judges its own findings against the bar; only a crossed bar produces
    the ALERT marker, which notifiers escalate (bran.notify) and the UI badges.
    Cron stays the sampling rate — alert changes delivery prominence, so a
    frequent quiet runner doesn't spam."""
    from bran.notify import ALERT_MARKER

    return (
        "## Alert mode — significance bar\n"
        "This is a sensing run. The user does not want noise; they want to be "
        "interrupted only when the bar below is crossed.\n\n"
        f"<significance_bar>\n{condition.strip()}\n</significance_bar>\n\n"
        "After gathering your findings, judge them against that bar.\n"
        f"- Bar crossed → your final report MUST start with the line "
        f"`{ALERT_MARKER}: <one sentence — what crossed the bar and why it "
        "matters>` followed by the supporting detail. Reserve this strictly "
        "for a genuine crossing.\n"
        "- Bar not crossed → produce your normal report WITHOUT that marker "
        "and note in one line that nothing crossed the alert bar. Never use "
        "the marker for near-misses; mention those as 'approaching the bar' "
        "in the body instead."
    )


# A report is reviewed whole. The reviewer used to see only its first 12,000
# characters with a "truncated" marker, and a full morning brief runs to
# ~16,000: it failed every brief as "cut off mid-sentence", which also cost a
# pointless corrective re-run each day. This cap is only a guard against a
# runaway output; a report that long keeps its opening and its ending.
_REVIEW_CAP = 120_000


def _verification_prompt(
    task: str, output: str, today: str | None = None, context: str | None = None,
) -> str:
    from datetime import datetime, timezone

    cut_note = ""
    if len(output) > _REVIEW_CAP:
        head, tail = output[: _REVIEW_CAP * 3 // 4], output[-_REVIEW_CAP // 4 :]
        output = head + "\n\n[… middle of the report left out of this review copy …]\n\n" + tail
        cut_note = (
            "The copy above leaves out the middle of a very long report. That "
            "cut was made for this review, not by the agent: do not count it "
            "as missing or incomplete content.\n"
        )
    today = today or datetime.now(timezone.utc).strftime("%A %d %B %Y")
    # What else the agent was told (its project, its teammate brief, the
    # previous report in delta mode). Without it the reviewer calls a mention
    # of the user's own project an invented assumption.
    background = ""
    if context and context.strip():
        ctx = context.strip()
        if len(ctx) > 12_000:
            ctx = ctx[:12_000] + "\n[… rest of the background left out …]"
        background = (
            "The agent was also given the background below. Referring to it is "
            "not fabrication.\n<background>\n" + ctx + "\n</background>\n\n"
        )
    return (
        "Review the agent output below against the task it was given.\n\n"
        f"<task>\n{task}\n</task>\n\n{background}<output>\n{output}\n</output>\n\n"
        f"{cut_note}"
        f"Today is {today}. Dates up to today are not in the future, and you "
        "cannot open links or look anything up, so do not fail the output "
        "because its events or links are newer than you know of.\n"
        "Judge ONLY: did the output actually do what the task asked (structure, "
        "scope, completeness), and is it internally consistent (no obvious "
        "fabrication, no placeholder/error text passed off as content)? Style "
        "preferences are not failures. Reply with EXACTLY one JSON object, no "
        "other text:\n"
        '{"pass": true|false, "issues": ["<each concrete problem>"], '
        '"feedback": "<one short paragraph for the agent to fix the issues>"}'
    )


async def _verify_output(task: str, output: str, context: str | None = None) -> dict | None:
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
        # allowed_tools=[] would only mean "pre-approve nothing"; this removes
        # every tool from the request. No filesystem settings either — a
        # JSON-only critic must not inherit CLAUDE.md, hooks or MCP servers.
        allowed_tools=[],
        disallowed_tools=["*"],
        setting_sources=[],
        permission_mode="dontAsk",
        model="haiku",
        max_turns=1,
        max_budget_usd=0.5,
    )
    async def _collect() -> str:
        out = ""
        async for msg in query(prompt=_verification_prompt(task, output, context=context), options=options):
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
    extra_tools: list[str] | None = None,
) -> None:
    """Verify a completed scheduled run; on a failed verdict, re-run ONCE with
    the reviewer's feedback and verify that attempt too. Verdicts are stored in
    each run's metadata['verification'] (shown on the run detail page). Never
    raises — verification is a quality layer, not a failure mode."""
    from bran.persistence import update_run

    try:
        verdict = await _verify_output(task, record.result or "", append_system)
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
            extra_tools=extra_tools,
        )
        record.metadata["verification"]["retried_run_id"] = retry.id
        update_run(record)
        if retry.status == "completed":
            second = await _verify_output(task, retry.result or "", append_system)
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
    catch_up_for: str | None = None,
) -> None:
    """Run one fire of a runner. `catch_up_for` is the slot (ISO time) this
    fire makes up for when it was missed while the server was down."""
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
    from bran.teammates import TEAM_TOOLS, run_context

    # A teammate's run can ask before changing its job, and read its reports.
    extra_tools = list(TEAM_TOOLS) if (rec is not None and rec.teammate_id) else None

    append_parts = [
        p for p in (
            # A teammate's automation runs as the teammate: its brief, and
            # what the user has told it in its chat.
            run_context(rec.teammate_id) if rec is not None else None,
            _project_append_system(project_id),
            _delta_append_system(schedule_id) if (rec is not None and rec.delta) else None,
            _alert_append_system(rec.alert) if (rec is not None and rec.alert.strip()) else None,
        ) if p
    ]
    append_system = "\n\n".join(append_parts) if append_parts else None

    failed = False
    # A verify-mode runner's report is not announced until the critic has seen
    # it: otherwise the phone gets the unchecked report, then the corrected
    # one. A failed run is announced at once either way (see runner._drive).
    verifying = rec is not None and rec.verify
    try:
        record = await run_agent(
            agent, task, project_id=project_id, source="runner",
            schedule_id=schedule_id, append_system=append_system,
            notify=not verifying, extra_tools=extra_tools,
        )
        failed = record.status != "completed"
        if catch_up_for:
            from bran.persistence import update_run

            record.metadata["catch_up_for"] = catch_up_for
            update_run(record)
        if not failed and verifying:
            await _run_verification(
                record, agent, task, project_id, schedule_id, append_system, extra_tools
            )
            # Passed, or the critic itself failed (fails open): deliver this
            # report. Rejected: the corrective re-run announced itself, and its
            # report is the one to read.
            if not (record.metadata.get("verification") or {}).get("retried_run_id"):
                from bran.notify import notify_completion

                await notify_completion(record)
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


# ---------------------------------------------------------------------------
# Catch-up. Jobs live in memory, so a restart recomputes every next fire from
# "now" — a slot that fell while the server was down is simply gone unless we
# look for it. A heartbeat records when the scheduler was last alive; at
# startup each recurring runner that had a slot between then and now (within
# BRAN_CATCHUP_HOURS) and has not run since is fired once.
#
# The same happens when the machine sleeps with the server still up: on waking,
# APScheduler drops every cron fire that is more than its grace period late.
# The heartbeat notices the gap in its own beats and looks for those slots.
# ---------------------------------------------------------------------------

_ALIVE_KEY = "scheduler_alive_at"
_HEARTBEAT_S = 60
# A gap this long between beats means the machine was asleep, not busy.
_WAKE_GAP_S = 300
# How late a cron fire may start and still be run by APScheduler itself.
_CRON_GRACE_S = 3600
# How far back "this automation did not run when it should have" looks.
_MISSED_LOOKBACK_DAYS = 7
# What the last startup did about missed slots (shown by /spa/health).
_startup_report: dict[str, list[dict[str, str]]] = {"caught_up": [], "skipped": []}


def last_due(cron: str, since, now):
    """The most recent moment in (since, now] at which `cron` was due, or None.
    `since`/`now` are timezone-aware datetimes."""
    trigger = _trigger_from_cron(cron)
    last = None
    due = trigger.get_next_fire_time(None, since)
    for _ in range(20_000):  # a per-minute cron over the longest window is ~1.4k
        if due is None or due > now:
            break
        if due > since:
            last = due
        due = trigger.get_next_fire_time(due, now)
    return last


def plan_catch_up(now=None) -> dict[str, list[dict[str, str]]]:
    """Which recurring runners missed a slot while the server was down.
    {"caught_up": [...]} are due a run now; {"skipped": [...]} missed one too
    long ago to be worth running. Each entry: {name, due}. Pure planning —
    nothing is fired here."""
    from datetime import datetime, timedelta, timezone

    from bran.persistence import get_setting, list_runs

    report: dict[str, list[dict[str, str]]] = {"caught_up": [], "skipped": []}
    hours = SETTINGS.catchup_hours
    alive_raw = get_setting(_ALIVE_KEY)
    if hours <= 0 or not alive_raw:
        return report  # off, or no record of when the scheduler last ran
    now = now or datetime.now(timezone.utc)
    try:
        alive = datetime.fromisoformat(alive_raw)
    except ValueError:
        return report
    window_start = now - timedelta(hours=hours)
    for rec in list_schedules():
        if not rec.enabled or rec.run_at:
            continue  # one-shots are covered by their unlimited misfire grace
        try:
            created = datetime.fromisoformat(rec.created_at)
            since = max(alive, created)
            if since >= now:
                continue
            due = last_due(rec.cron, max(since, window_start), now)
            if due is None:
                if since < window_start and last_due_exists(rec.cron, since, window_start):
                    report["skipped"].append({"name": rec.name, "due": ""})
                continue
            prior = list_runs(schedule_id=rec.id, limit=1)
            if prior and datetime.fromisoformat(prior[0].started_at) >= due:
                continue  # it ran after all (a retry, or "run now")
        except (ValueError, TypeError):
            log.exception("catch-up: could not evaluate runner %s", rec.name)
            continue
        report["caught_up"].append({"name": rec.name, "due": due.astimezone(timezone.utc).isoformat()})
    return report


def last_due_exists(cron: str, since, until) -> bool:
    """True when `cron` had at least one slot in (since, until]."""
    from datetime import timedelta

    due = _trigger_from_cron(cron).get_next_fire_time(None, since + timedelta(microseconds=1))
    return due is not None and due <= until


def _queue_catch_up(scheduler: AsyncIOScheduler, after_sleep: bool = False) -> None:
    """Fire once each recurring runner that missed a slot. `after_sleep`: the
    server stayed up but the machine slept, so APScheduler is itself running
    the fires that are still within its grace period — leave those to it."""
    from datetime import datetime, timedelta, timezone

    from apscheduler.triggers.date import DateTrigger

    from bran.persistence import get_schedule

    global _startup_report
    try:
        report = plan_catch_up()
    except Exception:
        log.exception("catch-up planning failed")
        return
    if after_sleep:
        recent = datetime.now(timezone.utc) - timedelta(seconds=_CRON_GRACE_S)
        report["caught_up"] = [
            e for e in report["caught_up"] if datetime.fromisoformat(e["due"]) < recent
        ]
    _startup_report = report
    why = "the computer was asleep" if after_sleep else "the server was down"
    for entry in _startup_report["skipped"]:
        log.warning(
            "runner %s missed a fire more than %dh ago while %s — not catching up",
            entry["name"], SETTINGS.catchup_hours, why,
        )
    for i, entry in enumerate(_startup_report["caught_up"]):
        rec = get_schedule(entry["name"])
        if rec is None:
            continue
        log.warning("runner %s missed its %s fire while %s — running it now",
                    rec.name, entry["due"], why)
        scheduler.add_job(
            _fire,
            # Staggered a little so a long outage doesn't start everything in
            # the same instant (the run-slot gate bounds concurrency anyway).
            trigger=DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(seconds=5 + 10 * i)),
            args=(rec.agent, rec.task, rec.name, rec.project_id, rec.id, False, 0, entry["due"]),
            id=f"bran-catchup:{rec.name}",
            name=f"{rec.name} (catch-up)",
            replace_existing=True,
            misfire_grace_time=None,
        )


def _heartbeat(check_gap: bool = True) -> None:
    """Record that the scheduler is alive. With `check_gap` (the minute-by-
    minute job), a long silence since the last beat means the machine slept:
    look for the fires that fell in it before recording the new beat."""
    from datetime import datetime, timezone

    from bran.persistence import get_setting, set_setting, utcnow_iso

    try:
        last = get_setting(_ALIVE_KEY) if check_gap else None
        if last and _scheduler is not None:
            gap = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
            if gap > _WAKE_GAP_S:
                log.warning("no heartbeat for %d min: the computer was asleep — checking for missed fires",
                            gap // 60)
                _queue_catch_up(_scheduler, after_sleep=True)
        set_setting(_ALIVE_KEY, utcnow_iso())
    except Exception:
        log.exception("scheduler heartbeat failed")


def missed_slot(rec: ScheduleRecord, now=None) -> str | None:
    """The latest moment (UTC ISO-8601) in the last week at which this
    recurring runner was due and has not run since, or None. It clears as soon
    as the runner next runs, on schedule or by hand. A slot so recent that it
    may still be about to start does not count."""
    from datetime import datetime, timedelta, timezone

    from bran.persistence import list_runs

    if not rec.enabled or rec.run_at:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        created = datetime.fromisoformat(rec.created_at)
        until = now - timedelta(seconds=_CRON_GRACE_S + _WAKE_GAP_S)
        due = None
        # Nearest window first: a frequent cron is answered in a few steps.
        for hours in (2, 26, 24 * _MISSED_LOOKBACK_DAYS):
            since = max(until - timedelta(hours=hours), created)
            if since >= until:
                return None
            due = last_due(rec.cron, since, until)
            if due is not None:
                break
        if due is None:
            return None
        prior = list_runs(schedule_id=rec.id, limit=1)
        if prior and datetime.fromisoformat(prior[0].started_at) >= due:
            return None
    except (ValueError, TypeError):
        return None
    return due.astimezone(timezone.utc).isoformat()


async def _nightly_prune() -> None:
    """Retention: drop old stored transcripts and stale chat attachments."""

    def work() -> tuple[int, int]:
        from bran.persistence import prune_run_events
        from bran.project_files import prune_uploads

        return (
            prune_run_events(SETTINGS.events_retention_days),
            prune_uploads(SETTINGS.uploads_retention_days),
        )

    try:
        events, uploads = await asyncio.to_thread(work)
        if events or uploads:
            log.info("nightly prune: dropped %d stored transcript(s), %d attachment(s)", events, uploads)
    except Exception:
        log.exception("nightly prune failed")


def scheduler_status() -> dict[str, object]:
    """For /spa/health: is the scheduler running, when was it last alive, and
    what the last startup did about fires missed during downtime."""
    from bran.persistence import get_setting

    return {
        "running": _scheduler is not None,
        "alive_at": get_setting(_ALIVE_KEY) or None,
        "catchup_hours": SETTINGS.catchup_hours,
        "caught_up": list(_startup_report["caught_up"]),
        "skipped": list(_startup_report["skipped"]),
    }


def start_scheduler() -> None:
    """Spin up APScheduler, register all persisted schedules, and make up
    for recurring fires missed while the server was down."""
    global _scheduler
    if _scheduler is not None:
        return
    from apscheduler.triggers.interval import IntervalTrigger

    _scheduler = AsyncIOScheduler()
    for rec in list_schedules():
        if rec.enabled:
            _add_job(_scheduler, rec)
    _queue_catch_up(_scheduler)       # reads the previous process's heartbeat…
    _heartbeat(check_gap=False)       # …before this one overwrites it
    _scheduler.add_job(
        _heartbeat, trigger=IntervalTrigger(seconds=_HEARTBEAT_S),
        id="bran-internal:heartbeat", replace_existing=True, coalesce=True, max_instances=1,
    )
    _scheduler.add_job(
        _nightly_prune, trigger=CronTrigger(hour=3, minute=30),
        id="bran-internal:prune", replace_existing=True, coalesce=True, max_instances=1,
    )
    _scheduler.start()
    log.info("scheduler started with %d jobs", len(_scheduler.get_jobs()))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _heartbeat(check_gap=False)  # a clean stop is "alive until now"
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
    # Misfire policy: a one-shot must fire even if its moment passed while the
    # server was down (grace=None = unlimited) — _fire disables it afterwards,
    # so a still-enabled past-due one-shot re-registering on every restart and
    # never firing (a "zombie") can't happen. Cron jobs get an hour, which only
    # covers a fire delayed while the process is UP (a busy loop): jobs live in
    # memory, so a slot missed while the server was down is not a misfire to
    # APScheduler at all — _queue_catch_up handles those at startup.
    scheduler.add_job(
        _fire,
        trigger=trigger,
        args=(rec.agent, rec.task, rec.name, rec.project_id, rec.id, once),
        id=_job_id(rec.name),
        name=rec.name,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=None if once else _CRON_GRACE_S,
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
