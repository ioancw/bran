"""The tools every teammate gets on top of its role's own.

Exposed as the `bran_team` MCP server; tools become `mcp__bran_team__<name>`.
They are added to a run only when it is working as a teammate (a turn of its
chat, or one of its automations): see `bran.teammates.TEAM_TOOLS`.

- ask_approval: ask the user before changing the job. Nothing happens until
  the user answers in the teammate's chat; on a yes, bran makes the change.
- read_delivery: the whole of a report the teammate delivered earlier.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from bran.background import current_run_id


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}], "is_error": True}


def _me():
    """(teammate, run) for the run this tool was called from."""
    from bran.persistence import get_run
    from bran.teammates import teammate_for_run

    run_id = current_run_id.get()
    run = get_run(run_id) if run_id else None
    return teammate_for_run(run), run


@tool(
    "ask_approval",
    (
        "Ask the user for a yes before you change your own job. The question appears in "
        "your chat with Approve and Decline; NOTHING changes until they answer, and on a "
        "yes bran makes the change itself. `title` is the question in one line; `detail` "
        "says why, in two or three sentences. `kind` says what a yes does:\n"
        "- note (default): nothing is changed for you. Use it for anything else you want "
        "a go-ahead for; once approved, you carry it out yourself from then on.\n"
        "- new_automation: adds a scheduled job of yours. Give `schedule` (plain words "
        "like 'every Friday at 4pm', or cron) and `task` (the full instructions for each "
        "run).\n"
        "- change_brief: replaces your standing brief. Give `brief`, the WHOLE new brief.\n"
        "- change_schedule: moves one of your automations. Give `schedule`, and "
        "`automation` (its name) when you have more than one.\n"
        "Do not ask again for something that is still waiting or was declined."
    ),
    {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "detail": {"type": "string"},
            "kind": {"type": "string", "enum": ["note", "new_automation", "change_brief", "change_schedule"]},
            "schedule": {"type": "string"},
            "task": {"type": "string"},
            "brief": {"type": "string"},
            "automation": {"type": "string"},
        },
        "required": ["title"],
    },
)
async def ask_approval(args: dict[str, Any]) -> dict[str, Any]:
    from bran.teammates import TeammateError, action_summary, ask

    mate, run = _me()
    if mate is None:
        return _err("this run is not working as a teammate, so there is nobody's chat to ask in.")
    try:
        q = ask(
            mate.id, title=args.get("title") or "", detail=args.get("detail") or "",
            kind=args.get("kind") or "note", schedule=args.get("schedule") or "",
            task=args.get("task") or "", brief=args.get("brief") or "",
            automation=args.get("automation") or "", run_id=run.id if run else None,
        )
    except TeammateError as e:
        return _err(str(e))
    does = action_summary(q.action)
    return _ok(
        f"Asked (request_id={q.id}). It is waiting in your chat for the user's answer"
        + (f"; a yes: {does}" if does else ".")
        + " Nothing has changed yet. Do not act on it and do not ask again."
    )


@tool(
    "read_delivery",
    (
        "Read the whole of a report you delivered earlier, by its run_id (the ids of your "
        "recent deliveries are in your instructions). Use it before answering a question "
        "about a report: what you were shown of it may be cut short."
    ),
    {"run_id": str},
)
async def read_delivery(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import get_run
    from bran.teammates import owns_run

    mate, _ = _me()
    if mate is None:
        return _err("this run is not working as a teammate.")
    run = get_run((args.get("run_id") or "").strip())
    if run is None or not owns_run(mate, run):
        return _err("no report of yours has that run_id.")
    text = (run.result or "").strip()
    if not text:
        return _err(f"that run ended {run.status} without a report.")
    return _ok(f"[delivered {run.ended_at or run.started_at}]\n{text}")


team_server = create_sdk_mcp_server(
    name="bran_team", version="0.1.0", tools=[ask_approval, read_delivery],
)
