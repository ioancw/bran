"""Teammates: an agent given a name and a standing job.

A teammate ties together things bran already had:

- a **role** — one of the agents;
- a **standing brief** — the job, in the user's words;
- **one chat** it is steered from, which never closes (the SDK session is
  resumed on every message; see `web.spa_routes.chats`);
- **automations** — schedules whose runs do its repeating work.

What its automations deliver shows up in the chat (`feed`), so a report can
be answered where it lands. And a teammate does not change its own job: it
asks (`ask`), the question waits in the chat, and on a yes bran makes the
change itself (`decide`).

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
import uuid
from typing import Any

from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    TeammateRecord,
    TeammateRequest,
    decide_teammate_request,
    delete_teammate,
    get_run,
    get_schedule,
    get_teammate,
    get_teammate_request,
    insert_teammate,
    insert_teammate_request,
    list_runs,
    list_schedules,
    list_teammate_requests,
    list_teammates,
    set_schedule_teammate,
    update_schedule,
    update_teammate,
)
from bran.schedule_service import ScheduleError, create_schedule, sync_job, validate_agent

_NAME_MAX = 60
_BRIEF_MAX = 8000
# How much of the chat a scheduled run is told about.
_FEEDBACK_MESSAGES = 12
_FEEDBACK_CHARS = 700
# How much of its own recent work the chat is told about.
_DELIVERIES_IN_PROMPT = 3
_DELIVERY_CHARS = 2500
# Asking before acting.
_TITLE_MAX = 140
_DETAIL_MAX = 2000
_NOTE_MAX = 1000
_MAX_PENDING = 3          # open questions one teammate may have at a time
_DECIDED_IN_PROMPT = 8    # answered questions it is reminded of
REQUEST_KINDS = ("note", "new_automation", "change_brief", "change_schedule")
# The tools every teammate gets on top of its role's (see bran.tools.team).
TEAM_TOOLS = ["mcp__bran_team__ask_approval", "mcp__bran_team__read_delivery"]


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
            # A report the reviewer sent back is not a delivery: the corrected
            # one that replaced it is.
            if (r.result or "").strip()
            and not (r.metadata.get("verification") or {}).get("retried_run_id")
        )
    out.sort(key=lambda r: r.started_at, reverse=True)
    return out[:limit]


def owns_run(teammate: TeammateRecord, run: RunRecord) -> bool:
    """Whether `run` is this teammate's work: a run of one of its automations,
    or a turn of its chat."""
    if run.schedule_id and any(s.id == run.schedule_id for s in automations_of(teammate.id)):
        return True
    return bool(teammate.chat_id) and run.session_id == teammate.chat_id


def teammate_for_run(run: RunRecord | None) -> TeammateRecord | None:
    """The teammate a run is working as: named on the run (a chat turn), or
    the owner of the automation that fired it."""
    if run is None:
        return None
    named = run.metadata.get("teammate_id")
    if named:
        return get_teammate(named)
    if run.schedule_id:
        for s in list_schedules():
            if s.id == run.schedule_id and s.teammate_id:
                return get_teammate(s.teammate_id)
    return None


def delivery_title(run: RunRecord, fallback: str = "Report") -> tuple[str, str]:
    """(title, excerpt) of a report: its first heading, else `fallback`. The
    excerpt is the report's own first words: not the agent's covering note
    above the heading ("Fact-check done, saved at …"), nor label lines,
    quoted notes, tables or section headings."""
    lines = [ln.strip() for ln in (run.result or "").splitlines() if ln.strip()]
    title = ""
    for i, ln in enumerate(lines[:6]):
        if ln.startswith("#"):
            title = ln.lstrip("#").strip().strip("*").strip()
            lines = lines[i + 1:]
            break
    if not title:
        title = fallback
    prose = [
        ln for ln in lines
        if not ln.startswith(("#", ">", "|", "```", "---")) and not re.match(r"\*\*[^*]+:\*\*", ln)
    ]
    body = " ".join(re.sub(r"[*`]", "", ln.lstrip("-*+ ")).strip() for ln in (prose or lines))
    return title[:120], _clip(body, 260)


def request_dict(r: TeammateRequest) -> dict[str, Any]:
    return {
        "id": r.id, "teammate_id": r.teammate_id, "run_id": r.run_id, "title": r.title,
        "detail": r.detail, "action": r.action, "status": r.status, "note": r.note,
        "outcome": r.outcome, "created_at": r.created_at, "decided_at": r.decided_at,
        "summary": action_summary(r.action),
    }


def feed(teammate: TeammateRecord, limit: int = 30) -> list[dict[str, Any]]:
    """What belongs in the teammate's chat besides the conversation itself,
    oldest first, as chat events: each report its automations delivered, and
    each thing it asked from a scheduled run. (A question asked in a chat turn
    is already in the conversation, as the tool call that asked it.)"""
    names = {s.id: s.name for s in automations_of(teammate.id)}
    out: list[dict[str, Any]] = []
    for r in deliveries(teammate, limit=limit):
        title, excerpt = delivery_title(r, names.get(r.schedule_id or "", "Report"))
        out.append({
            "type": "delivery", "run_id": r.id, "title": title, "excerpt": excerpt,
            "schedule": names.get(r.schedule_id or ""), "ts": r.ended_at or r.started_at,
        })
    for q in list_teammate_requests(teammate.id, limit=limit):
        asked_by = get_run(q.run_id) if q.run_id else None
        if asked_by is not None and asked_by.source == "chat":
            continue
        out.append({"type": "request", "request_id": q.id, "ts": q.created_at})
    out.sort(key=lambda e: e["ts"] or "")
    return out


def merge_feed(events: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place feed events in a conversation by time, between turns only: each
    goes just before the first user message sent after it, else at the end."""
    if not extra:
        return events
    out = list(events)
    for ev in extra:  # oldest first, so equal slots keep their order
        at = len(out)
        for i, e in enumerate(out):
            if e.get("type") == "user" and (e.get("ts") or "") > (ev.get("ts") or ""):
                at = i
                break
        out.insert(at, ev)
    return out


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

def _asking(teammate: TeammateRecord, *, in_chat: bool) -> str:
    """How it asks before acting, and what it has asked so far."""
    lines = [
        "### Asking before you change your job",
        "You do not change your own job on your own. When you think a change would serve the "
        "user (a new piece of scheduled work, a different brief, a different time, or anything "
        "else you want a yes for first), call `mcp__bran_team__ask_approval`. The question "
        "waits in your chat; on a yes, bran makes the change itself. "
        + (
            "The user is here, so use it for a change they have asked for too: it lets them "
            "confirm the exact wording with one click. After asking, say in a sentence what "
            "you asked and stop."
            if in_chat else
            "Then carry on and finish this run without it: the answer comes later. Ask "
            "rarely, at most once in a run, and only for something worth interrupting them for."
        ),
    ]
    asked = list_teammate_requests(teammate.id, limit=40)
    waiting = [q for q in asked if q.status == "pending"]
    answered = [q for q in asked if q.status != "pending"][:_DECIDED_IN_PROMPT]
    if waiting:
        lines.append(
            "\nWaiting for the user's answer (do not ask these again, and do not act on them):\n"
            + "\n".join(f"- {q.title}" for q in waiting)
        )
    if answered:
        rows = []
        for q in reversed(answered):
            row = f"- {q.title}: {q.status} on {(q.decided_at or '')[:10]}"
            if q.outcome:
                row += f". Done by bran: {q.outcome}"
            if q.note:
                row += f'. The user said: "{_clip(q.note, 300)}"'
            rows.append(row)
        lines.append(
            "\nAnswered, oldest first. An approved one with nothing done by bran is yours to "
            "carry out from now on; a declined one is not to be raised again unless something "
            "changes:\n" + "\n".join(rows)
        )
    return "\n".join(lines)


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
    parts.append(_asking(teammate, in_chat=False))
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
            "Newest first; long reports are cut short here. The user sees each one in this "
            "chat as a card and may be replying to it: when they ask about a report, read all "
            "of it with `mcp__bran_team__read_delivery` (give the run_id) before answering.\n\n"
            + "\n\n".join(blocks)
        )
    parts.append(_asking(teammate, in_chat=True))
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


# --- asking before acting ---------------------------------------------------------

def action_summary(action: dict[str, Any]) -> str:
    """What approving does, in a line ("" for a plain question)."""
    from bran.nl_cron import humanize_cron

    kind = action.get("kind")
    if kind == "new_automation":
        return f"Adds an automation: {humanize_cron(action.get('cron') or '')}."
    if kind == "change_brief":
        return "Replaces the standing brief."
    if kind == "change_schedule":
        return f"Moves `{action.get('automation')}` to: {humanize_cron(action.get('cron') or '')}."
    return ""


def ask(
    teammate_id: str, *, title: str, detail: str = "", kind: str = "note",
    schedule: str = "", task: str = "", brief: str = "", automation: str = "",
    run_id: str | None = None,
) -> TeammateRequest:
    """Record something the teammate wants a yes for. Everything a later
    approval needs is checked now, so a yes cannot fail on the wording.
    Raises TeammateError (its message is written for the agent)."""
    from bran.schedule_service import resolve_trigger

    mate = get_teammate(teammate_id)
    if mate is None:
        raise TeammateError("No such teammate.", kind="missing")
    title = " ".join((title or "").split())[:_TITLE_MAX]
    if not title:
        raise TeammateError("`title` is required: one line saying what you are asking.")
    kind = (kind or "note").strip() or "note"
    if kind not in REQUEST_KINDS:
        raise TeammateError(f"`kind` must be one of: {', '.join(REQUEST_KINDS)}.")
    pending = list_teammate_requests(teammate_id, status="pending")
    if any(q.title.lower() == title.lower() for q in pending):
        raise TeammateError("You have already asked this and it is waiting for the user's answer.")
    if len(pending) >= _MAX_PENDING:
        raise TeammateError(
            f"You already have {len(pending)} questions waiting for the user. Do not ask more "
            "until they answer."
        )

    action: dict[str, Any] = {"kind": kind}
    if kind in ("new_automation", "change_schedule"):
        try:
            cron, _ = resolve_trigger((schedule or "").strip(), None)
        except ScheduleError as e:
            raise TeammateError(f"`schedule`: {e}")
        action["cron"] = cron
    if kind == "new_automation":
        task = (task or "").strip()
        if not task:
            raise TeammateError("`task` is required for new_automation: what each run should do.")
        action["task"] = task[:_BRIEF_MAX]
    elif kind == "change_brief":
        brief = (brief or "").strip()
        if not brief:
            raise TeammateError("`brief` is required for change_brief: the whole new brief.")
        action["brief"] = brief[:_BRIEF_MAX]
    elif kind == "change_schedule":
        mine = automations_of(teammate_id)
        name = (automation or "").strip() or (mine[0].name if len(mine) == 1 else "")
        if not any(s.name == name for s in mine):
            known = ", ".join(s.name for s in mine) or "none"
            raise TeammateError(f"`automation` must name one of your automations ({known}).")
        action["automation"] = name

    rec = TeammateRequest(
        id=str(uuid.uuid4()), teammate_id=teammate_id, title=title,
        detail=(detail or "").strip()[:_DETAIL_MAX], action=action, run_id=run_id,
    )
    insert_teammate_request(rec)
    return rec


def _carry_out(mate: TeammateRecord, q: TeammateRequest) -> str:
    """Make the change an approved request asked for; returns what was done."""
    from bran.nl_cron import humanize_cron

    a = q.action
    kind = a.get("kind")
    if kind == "new_automation":
        try:
            rec = create_schedule(
                name=_free_schedule_name(q.title), agent=mate.agent, task=a.get("task") or "",
                cron=a.get("cron") or "", project_id=mate.project_id, teammate_id=mate.id,
            )
        except ScheduleError as e:
            raise TeammateError(str(e))
        return f"added the automation `{rec.name}` ({humanize_cron(rec.cron)})"
    if kind == "change_brief":
        update_teammate(mate.id, name=mate.name, agent=mate.agent, brief=a.get("brief") or "")
        return "replaced the standing brief"
    if kind == "change_schedule":
        current = get_schedule(a.get("automation") or "")
        if current is None or current.teammate_id != mate.id:
            raise TeammateError(
                f"The automation {a.get('automation')!r} is no longer this teammate's."
            )
        rec = update_schedule(
            current.name, agent=current.agent, task=current.task, cron=a.get("cron") or "",
            run_at=None,
        )
        assert rec is not None
        sync_job(rec)
        return f"moved `{rec.name}` to {humanize_cron(rec.cron)}"
    return ""


def decide(request_id: str, *, approve: bool, note: str = "") -> TeammateRequest:
    """The user's answer. A yes makes the change the request described; if
    that cannot be done the request stays open and the reason is raised."""
    q = get_teammate_request(request_id)
    if q is None:
        raise TeammateError("No such request.", kind="missing")
    if q.status != "pending":
        raise TeammateError(f"This was already {q.status}.", kind="conflict")
    mate = get_teammate(q.teammate_id)
    if mate is None:
        raise TeammateError("No such teammate.", kind="missing")
    outcome = _carry_out(mate, q) if approve else ""
    done = decide_teammate_request(
        request_id, status="approved" if approve else "declined",
        note=(note or "").strip()[:_NOTE_MAX], outcome=outcome,
    )
    if done is None:
        raise TeammateError("This was already answered.", kind="conflict")
    return done


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
        # How many of its questions are waiting for the user.
        "waiting": len(list_teammate_requests(t.id, status="pending")),
    }


__all__ = [
    "REQUEST_KINDS", "TEAM_TOOLS", "TeammateError", "action_summary", "ask", "assign_automation",
    "automations_of", "chat_context", "create_teammate", "decide", "deliveries", "delivery_title",
    "edit_teammate", "feed", "list_teammates", "merge_feed", "owns_run", "request_dict",
    "run_context", "state_of", "teammate_dict", "teammate_for_run",
]
