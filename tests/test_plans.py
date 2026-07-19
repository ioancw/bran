"""propose_plan: the plan-approval gate is conversational — the tool only
validates input and instructs the agent to stop; the card is the tool call."""

from __future__ import annotations

import asyncio

from bran.tools.plans import propose_plan


def _call(args: dict) -> str:
    result = asyncio.run(propose_plan.handler(args))
    return result["content"][0]["text"]


def test_propose_plan_instructs_agent_to_stop():
    text = _call({"title": "Set up runners", "plan": "1. Create runner A\n2. Pause runner B"})
    assert "STOP" in text
    assert "approv" in text.lower()  # approves/approval — the gate semantics


def test_propose_plan_requires_a_plan():
    assert _call({"title": "x", "plan": "  "}).startswith("Error:")
    assert _call({}).startswith("Error:")


def test_propose_plan_registered_on_bran_server():
    from bran.agents import get_agent
    from bran.tools.spawn import spawn_agent_server  # noqa: F401 — import side check

    assert "mcp__bran__propose_plan" in get_agent("orchestrator").tools
