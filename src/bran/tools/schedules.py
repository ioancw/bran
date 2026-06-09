"""In-process MCP tools for managing Runners (scheduled agents) from chat.

Lets the orchestrator create / list / pause / resume / delete scheduled runners
*in conversation* — so the user can say "schedule the finance-news agent every
weekday at 7am" and it's set up, instead of leaving the chat to fill a form.
(Cowork's `create_scheduled_task`, adapted to bran's fleet model.)

Exposed via the `bran` MCP server; tools become `mcp__bran__<name>`. Each tool
keeps the live APScheduler in sync (register/unregister) when bran serve is
running, and is a harmless no-op for the DB-only paths otherwise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from claude_agent_sdk import tool


# --- helpers ---------------------------------------------------------------

def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}]}


def _parse_schedule(s: str) -> tuple[str, str | None]:
    """Map a schedule string to (cron, run_at).

    An ISO-8601 datetime -> one-shot ("", run_at). Otherwise a 5-field cron OR
    natural language ("every weekday at 9am") -> recurring (cron, None), via
    `nl_cron`. Raises ValueError (with a recoverable suggestion) if neither.
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("a schedule is required (cron, natural language, or an ISO datetime)")
    try:
        datetime.fromisoformat(s)
        return "", s  # one-shot
    except ValueError:
        pass
    from bran.nl_cron import NlCronParseError, parse

    try:
        return parse(s).cron, None  # cron or natural language
    except NlCronParseError as e:
        raise ValueError(str(e))


def _when(rec: Any) -> str:
    if rec.run_at:
        return f"once at {rec.run_at}"
    from bran.nl_cron import humanize_cron

    return humanize_cron(rec.cron)


def _register(rec: Any) -> None:
    try:
        from bran.scheduler import register_schedule

        register_schedule(rec)
    except Exception:
        pass  # no live scheduler (e.g. --no-scheduler / CLI) — DB row is enough


def _unregister(name: str) -> None:
    try:
        from bran.scheduler import unregister_schedule

        unregister_schedule(name)
    except Exception:
        pass


def _next_run(rec: Any) -> str:
    try:
        from bran.scheduler import next_run_for

        nxt = rec.run_at if rec.run_at else next_run_for(rec.cron)
        return f"Next run: {nxt}." if nxt else "It is not currently scheduled to fire."
    except Exception:
        return ""


# --- tools -----------------------------------------------------------------

@tool(
    "create_runner",
    (
        "Create a Runner — a scheduled, autonomous run of an agent — without the "
        "user leaving the chat. Use this when the user asks to 'schedule', "
        "'set up a recurring task', 'every morning/weekday', 'remind me to run X', "
        "etc. Distinct from spawn_agent (which runs ONCE, now, in the background): "
        "create_runner sets up something that fires later, on a schedule.\n"
        "Arguments:\n"
        "- name: a short unique slug (e.g. 'morning-brief'). Must not already exist.\n"
        "- agent: a known agent name (e.g. 'finance-news', 'research', 'orchestrator').\n"
        "- task: the prompt the agent runs each time it fires.\n"
        "- schedule: for a recurring runner, EITHER plain English ('every weekday "
        "at 9am', 'daily at 18:30', 'every 2 hours', 'every Monday at 8:30') OR a "
        "5-field cron expression ('0 9 * * mon-fri'); OR, for a one-shot run, an "
        "ISO-8601 datetime ('2026-06-10T09:00:00'). You can pass the user's own "
        "words through. Confirm the resolved time back to them.\n"
        "- project_id: the project to attach the runner to (it then runs with that "
        "project's memory); pass an empty string for a standalone runner. The "
        "current project's id, if any, is in your system prompt — do not invent one.\n"
        "Confirm the details back to the user (agent, when, next fire time)."
    ),
    {"name": str, "agent": str, "task": str, "schedule": str, "project_id": str},
)
async def create_runner(args: dict[str, Any]) -> dict[str, Any]:
    from bran.agents import get_agent
    from bran.persistence import ScheduleRecord, get_schedule, insert_schedule

    name = (args.get("name") or "").strip()
    agent = (args.get("agent") or "").strip()
    task = (args.get("task") or "").strip()
    project_id = (args.get("project_id") or "").strip() or None

    if not name:
        return _err("a unique name is required.")
    if not agent:
        return _err("an agent name is required.")
    try:
        get_agent(agent)
    except KeyError:
        return _err(f"unknown agent {agent!r}. List agents first if unsure.")
    if get_schedule(name) is not None:
        return _err(f"a runner named {name!r} already exists — pick another name or delete it first.")
    try:
        cron, run_at = _parse_schedule(args.get("schedule") or "")
    except ValueError as e:
        return _err(str(e))

    rec = ScheduleRecord.new(
        name=name, agent=agent, task=task, cron=cron,
        project_id=project_id, run_at=run_at,
    )
    insert_schedule(rec)
    _register(rec)
    scope = f" (in project {project_id})" if project_id else ""
    return _ok(f"Created runner '{name}': {agent} runs {_when(rec)}{scope}. {_next_run(rec)}")


@tool(
    "list_runners",
    (
        "List the scheduled Runners. Pass project_id to list only a project's "
        "runners, or an empty string for all. Use before creating (to check the "
        "name is free) or when the user asks what's scheduled."
    ),
    {"project_id": str},
)
async def list_runners(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import list_schedules

    pid = (args.get("project_id") or "").strip() or None
    rows = list_schedules(project_id=pid)
    if not rows:
        return _ok("No runners scheduled.")
    lines = [
        f"- {s.name}: {s.agent} {_when(s)} "
        f"[{'enabled' if s.enabled else 'paused'}] — {s.task or '(no task)'}"
        for s in rows
    ]
    return _ok("Runners:\n" + "\n".join(lines))


@tool(
    "pause_runner",
    "Pause a Runner so it stops firing (it is kept, not deleted). Resume later with resume_runner.",
    {"name": str},
)
async def pause_runner(args: dict[str, Any]) -> dict[str, Any]:
    return await _set_enabled((args.get("name") or "").strip(), False)


@tool(
    "resume_runner",
    "Resume a paused Runner so it fires on its schedule again.",
    {"name": str},
)
async def resume_runner(args: dict[str, Any]) -> dict[str, Any]:
    return await _set_enabled((args.get("name") or "").strip(), True)


async def _set_enabled(name: str, enabled: bool) -> dict[str, Any]:
    from bran.persistence import set_schedule_enabled

    rec = set_schedule_enabled(name, enabled)
    if rec is None:
        return _err(f"no runner named {name!r}.")
    if enabled:
        _register(rec)
    else:
        _unregister(name)
    verb = "Resumed" if enabled else "Paused"
    tail = f" {_next_run(rec)}" if enabled else ""
    return _ok(f"{verb} runner '{name}'.{tail}")


@tool(
    "delete_runner",
    "Delete a Runner permanently. The user must clearly want it removed.",
    {"name": str},
)
async def delete_runner(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import delete_schedule

    name = (args.get("name") or "").strip()
    if not delete_schedule(name):
        return _err(f"no runner named {name!r}.")
    _unregister(name)
    return _ok(f"Deleted runner '{name}'.")


# All runner tools, for the `bran` MCP server to splat into its tool list.
RUNNER_TOOLS = [create_runner, list_runners, pause_runner, resume_runner, delete_runner]
