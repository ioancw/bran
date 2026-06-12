"""Plan-approval gate — the Cowork-style "propose, pause, get a go-ahead".

`propose_plan` doesn't execute anything: the plan card the user approves is the
tool *call* itself, rendered by the chat UI (PlanCard) from the tool input. The
tool result just instructs the agent to end its turn — the "pause" is simply
the turn ending, and approval is the user's next message resuming the session.
No new run-loop machinery; works on the existing chat stream + history replay.

Exposed via the `bran` MCP server; becomes `mcp__bran__propose_plan`.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}]}


@tool(
    "propose_plan",
    (
        "Present a plan to the user and PAUSE for their approval before acting. "
        "Use this BEFORE significant or hard-to-reverse work: creating, editing "
        "or deleting runners; fanning out 3+ background runs; multi-step jobs "
        "with side effects, meaningful cost, or file writes. Do NOT gate "
        "trivial, read-only, or single-step actions — don't make the user "
        "approve everything. `title` is a short label; `plan` is concise "
        "markdown (numbered steps, one line each; note risks or costs). After "
        "calling this, END YOUR TURN with at most one short sentence. Do not "
        "execute ANY step until the user replies approving. If they ask for "
        "changes, revise and propose again."
    ),
    {"title": str, "plan": str},
)
async def propose_plan(args: dict[str, Any]) -> dict[str, Any]:
    plan = (args.get("plan") or "").strip()
    if not plan:
        return _err("a non-empty `plan` is required.")
    return _ok(
        "Plan presented to the user as an approval card. STOP: end your turn "
        "now with at most one short sentence (e.g. 'Waiting for your "
        "go-ahead.'). Execute only after the user approves; if they request "
        "changes, revise and call propose_plan again."
    )


PLAN_TOOLS = [propose_plan]
