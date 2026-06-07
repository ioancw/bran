"""In-process MCP tools for reading run results — the *fan-in* half of
multi-agent work. spawn_agent fans work out into background runs; these let the
orchestrator collect and synthesise those results back in the conversation.

Exposed via the `bran` MCP server; tools become `mcp__bran__<name>`.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}]}


def _cost(rec: Any) -> str:
    return f"${rec.total_cost_usd:.4f}" if rec.total_cost_usd is not None else "n/a"


@tool(
    "get_run_result",
    (
        "Read the result of a run by its id — use this to collect the output of "
        "background runs you fanned out with spawn_agent (which returned the "
        "run_id to you), then synthesise them into one answer. If the run is "
        "still pending/running you'll be told so — report that to the user and "
        "check back later; do NOT loop on it. On completion you get the full "
        "result text to fold into your reply."
    ),
    {"run_id": str},
)
async def get_run_result(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import get_run

    run_id = (args.get("run_id") or "").strip()
    if not run_id:
        return _err("a run_id is required.")
    rec = get_run(run_id)
    if rec is None:
        return _err(f"no run with id {run_id!r}.")
    if rec.status in ("pending", "running"):
        return _ok(f"Run {run_id} ({rec.agent}) is still {rec.status} — check back in a few seconds.")
    if rec.status == "failed":
        return _ok(f"Run {run_id} ({rec.agent}) FAILED: {rec.error or 'unknown error'}")
    if rec.status == "cancelled":
        return _ok(f"Run {run_id} ({rec.agent}) was cancelled before finishing.")
    body = rec.result or "(the run completed but produced no result text)"
    return _ok(
        f"Result of run {run_id} — agent={rec.agent}, cost={_cost(rec)}, turns={rec.num_turns}:\n\n{body}"
    )


@tool(
    "list_recent_runs",
    (
        "List recent runs so you can find the ids of work you fanned out. "
        "Optional filters: agent (e.g. 'research'), source ('spawn' = background "
        "fan-outs, 'runner' = scheduled, 'manual', 'chat'), and limit (default "
        "15). Returns id, status, agent, source and a task snippet."
    ),
    {"agent": str, "source": str, "limit": str},
)
async def list_recent_runs(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import list_runs

    agent = (args.get("agent") or "").strip() or None
    source = (args.get("source") or "").strip() or None
    try:
        limit = max(1, min(50, int(args.get("limit") or "15")))
    except (ValueError, TypeError):
        limit = 15
    rows = list_runs(agent=agent, limit=limit)
    if source:
        rows = [r for r in rows if r.source == source]
    if not rows:
        return _ok("No matching runs.")
    lines = [
        f"- {r.id} [{r.status}] {r.agent} ({r.source}) — {(r.task or '')[:80]}"
        for r in rows
    ]
    return _ok("Recent runs:\n" + "\n".join(lines))


# All run-reading tools, for the `bran` MCP server to splat into its tool list.
RUN_TOOLS = [get_run_result, list_recent_runs]
