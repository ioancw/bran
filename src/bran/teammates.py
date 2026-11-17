"""Teammates: an agent given a name and a standing job.

A teammate ties together things bran already had:

- a **role** — one of the agents;
- a **standing brief** — the job, in the user's words;
- **one chat** it is steered from, which never closes (the SDK session is
  resumed on every message; see `web.spa_routes.chats`);
- **automations** — schedules whose runs do its repeating work.

The chat and the scheduled runs are separate sessions (a daily briefing run
inside an ever-growing conversation would cost more every day), so what joins
them is the system prompt: a scheduled run is told the brief and what the
user last said in the chat, and the chat is told what the automations are
and what they last delivered. Replying in the chat is therefore how the
teammate's work is changed — the next run reads it.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    TeammateRecord,
    delete_teammate,
    get_schedule,
    get_teammate,
    insert_teammate,
    list_runs,
    list_schedules,
    list_teammates,
    set_schedule_teammate,
    update_teammate,
)
from bran.schedule_service import ScheduleError, create_schedule, validate_agent

_NAME_MAX = 60
_BRIEF_MAX = 8000
# How much of the chat a scheduled run is told about.
_FEEDBACK_MESSAGES = 12
_FEEDBACK_CHARS = 700
# How much of its own recent work the chat is told about.
_DELIVERIES_IN_PROMPT = 3
_DELIVERY_CHARS = 2500


class TeammateError(ValueError):
    """A teammate request the user can fix. `kind` is "invalid", "conflict"
    or "missing"."""

    def __init__(self, message: str, *, kind: str = "invalid") -> None:
        super().__init__(message)
        self.kind = kind


# --- reading ------------------------------------------------------------------

def automations_of(teammate_id: str) -> list[ScheduleRecord]:
    return [s for s in list_schedules() if s.teammate_id == teammate_id]


def deliveries(teammate: TeammateRecord, limit: int = 10) -> list[RunRecord]:
    """What its automations finished, newest first."""
    out: list[RunRecord] = []
    for s in automations_of(teammate.id):
        out.extend(
            r for r in list_runs(schedule_id=s.id, status="completed", limit=limit)
            if (r.result or "").strip()
        )
    out.sort(key=lambda r: r.started_at, reverse=True)
    return out[:limit]


def state_of(teammate: TeammateRecord, automations: list[ScheduleRecord]) -> str:
    """working (a run or a chat turn is in flight), failed (an automation's
    newest run failed), paused (it has automations and all are paused), else
    ready."""
    newest: list[RunRecord] = []
    for s in automations:
        newest.extend(list_runs(schedule_id=s.id, limit=1))
    if teammate.chat_id:
        newest.extend(
            r for r in list_runs(session_id=teammate.chat_id, limit=1) if r.status in ("running", "pending")
        )
    if any(r.status in ("running", "pending") for r in newest):
        return "working"
    if any(r.status == "failed" for r in newest):
        return "failed"
    if automations and not any(s.enabled for s in automations):
        return "paused"
    return "ready"


def _user_messages(chat_id: str | None) -> list[str]:
    """What the user wrote in the teammate's chat, oldest first."""
    if not chat_id:
        return []
    turns = [r for r in list_runs(session_id=chat_id, limit=60) if r.source == "chat"]
    said = [(r.task or "").strip() for r in turns[:_FEEDBACK_MESSAGES]]
    return [s for s in reversed(said) if s]


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


# --- what the agent is told ----------------------------------------------------

def _identity(teammate: TeammateRecord) -> str:
    lines = [
        "## You are a teammate",
        f'You are "{teammate.name}", a teammate of the user in bran: an agent with a standing job '
        "that you own from one run to the next.",
    ]
    brief = teammate.brief.strip()
    if brief:
        lines.append("\n### Your standing brief\n" + brief)
    return "\n".join(lines)


def run_context(teammate_id: str | None) -> str | None:
    """System-prompt suffix for one of a teammate's scheduled runs: who it is,
    its brief, and what the user has told it in its chat."""
    teammate = get_teammate(teammate_id) if teammate_id else None
    if teammate is None:
        return None
    parts = [_identity(teammate)]
    said = _user_messages(teammate.chat_id)
    if said:
        parts.append(
            "### What the user has told you\n"
            "These are the user's messages in your chat, oldest first. Treat them as standing "
            "feedback on how to do this job: where one asks for a change, apply it in this run "
            "and from now on; where a later message contradicts an earlier one, the later wins. "
            "Ignore any that are questions about a past report rather than instructions.\n\n"
            + "\n".join(f"- {_clip(s, _FEEDBACK_CHARS)}" for s in said)
        )
    return "\n\n".join(parts)


def chat_context(teammate: TeammateRecord) -> str:
    """System-prompt suffix for a message in the teammate's chat."""
    from bran.nl_cron import humanize_cron

    parts = [_identity(teammate)]
    autos = automations_of(teammate.id)
    if autos:
        rows = []
        for s in autos:
            when = f"once at {s.run_at}" if s.run_at else humanize_cron(s.cron)
            rows.append(
                f"- `{s.name}` — {when}{'' if s.enabled else ' (paused)'}: {_clip(s.task, 400)}"
            )
        parts.append("### Your automations\nYou do this repeating work on a schedule:\n" + "\n".join(rows))
    done = deliveries(teammate, limit=_DELIVERIES_IN_PROMPT)
    if done:
        blocks = [
            f'<delivery run_id="{r.id}" finished="{r.ended_at or r.started_at}">\n'
            f"{_clip(r.result or '', _DELIVERY_CHARS)}\n</delivery>"
            for r in done
        ]
        parts.append(
            "### What you delivered most recently\n"
            "Newest first; long reports are cut short here.\n\n" + "\n\n".join(blocks)
        )
    parts.append(
        "### This chat\n"
        "This is your one chat with the user; it stays open, and they come back to it to ask "
        "about your work or to change it. When they ask for a change to how you do the job, "
        "confirm in a sentence what you will do differently: your next scheduled run reads "
        "this chat and applies it. Work they hand you here, do now."
    )
    return "\n\n".join(parts)


# --- changing -------------------------------------------------------------------

def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise TeammateError("A teammate needs a name.")
    if len(name) > _NAME_MAX:
        raise TeammateError(f"A teammate's name can be at most {_NAME_MAX} characters.")
    return name


def _clean_agent(agent: str) -> str:
    try:
        return validate_agent(agent)
    except ScheduleError as e:
        raise TeammateError(str(e))


def _free_schedule_name(base: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40] or "teammate"
    name, n = stem, 2
    while get_schedule(name) is not None:
        name, n = f"{stem}-{n}", n + 1
    return name


def create_teammate(
    *, name: str, agent: str, brief: str = "", schedule: str = "", adopt: str = "",
    project_id: str | None = None,
) -> TeammateRecord:
    """Create a teammate. `schedule` (plain words or cron) gives it an
    automation that does the brief on that schedule; `adopt` names an existing
    automation to hand over to it instead. Raises TeammateError."""
    name = _clean_name(name)
    agent = _clean_agent(agent)
    brief = (brief or "").strip()[:_BRIEF_MAX]
    schedule = (schedule or "").strip()
    adopt = (adopt or "").strip()
    if schedule and not brief:
        raise TeammateError("Say what the job is: the schedule needs something to run.")
    existing = get_schedule(adopt) if adopt else None
    if adopt and existing is None:
        raise TeammateError(f"There is no automation named {adopt!r}.", kind="missing")
    if existing is not None and existing.teammate_id:
        raise TeammateError(f"{adopt!r} already belongs to another teammate.", kind="conflict")
    if schedule:
        # Check the schedule reads before anything is stored.
        from bran.schedule_service import resolve_trigger

        try:
            resolve_trigger(schedule, None)
        except ScheduleError as e:
            raise TeammateError(str(e))

    rec = TeammateRecord.new(name=name, agent=agent, brief=brief, project_id=project_id or None)
    try:
        insert_teammate(rec)
    except sqlite3.IntegrityError:
        raise TeammateError(f"A teammate named {name!r} already exists.", kind="conflict")
    if existing is not None:
        set_schedule_teammate(existing.name, rec.id)
    if schedule:
        try:
            create_schedule(
                name=_free_schedule_name(name), agent=agent, task=brief, cron=schedule,
                project_id=project_id or None, teammate_id=rec.id,
            )
        except ScheduleError as e:
            delete_teammate(rec.id)
            raise TeammateError(str(e))
    return rec


def edit_teammate(teammate_id: str, *, name: str, agent: str, brief: str) -> TeammateRecord:
    name = _clean_name(name)
    agent = _clean_agent(agent)
    try:
        rec = update_teammate(teammate_id, name=name, agent=agent, brief=(brief or "").strip()[:_BRIEF_MAX])
    except sqlite3.IntegrityError:
        raise TeammateError(f"A teammate named {name!r} already exists.", kind="conflict")
    if rec is None:
        raise TeammateError("No such teammate.", kind="missing")
    return rec


def assign_automation(teammate_id: str, schedule_name: str, attach: bool) -> ScheduleRecord:
    """Hand an existing automation to a teammate, or take it back."""
    if get_teammate(teammate_id) is None:
        raise TeammateError("No such teammate.", kind="missing")
    current = get_schedule(schedule_name)
    if current is None:
        raise TeammateError(f"There is no automation named {schedule_name!r}.", kind="missing")
    if attach and current.teammate_id not in (None, teammate_id):
        raise TeammateError(f"{schedule_name!r} already belongs to another teammate.", kind="conflict")
    if not attach and current.teammate_id != teammate_id:
        raise TeammateError(f"{schedule_name!r} does not belong to this teammate.")
    rec = set_schedule_teammate(schedule_name, teammate_id if attach else None)
    assert rec is not None
    return rec


def teammate_dict(t: TeammateRecord) -> dict[str, Any]:
    """A teammate as the UI reads it: itself, its automations (with next fire
    time), how it is doing, and when it is next due."""
    from bran.web.spa_routes.schedules import schedule_dict

    autos = automations_of(t.id)
    rows = [schedule_dict(s) for s in autos]
    due = sorted(r["next_run"] for r in rows if r["next_run"])
    return {
        "id": t.id, "name": t.name, "agent": t.agent, "brief": t.brief,
        "chat_id": t.chat_id, "project_id": t.project_id, "created_at": t.created_at,
        "automations": rows,
        "state": state_of(t, autos),
        "next_run": due[0] if due else None,
    }


__all__ = [
    "TeammateError", "assign_automation", "automations_of", "chat_context", "create_teammate",
    "deliveries", "edit_teammate", "list_teammates", "run_context", "state_of", "teammate_dict",
]
