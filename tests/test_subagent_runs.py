"""Inline Agent-tool delegations are recorded as child runs (source=subagent):
their prompt, result, inner events and the resumable agent id — and a parent
that dies first fails them instead of leaving them 'running'."""

from __future__ import annotations

import asyncio

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, UserMessage
from fastapi.testclient import TestClient

from bran.api import build_app
from bran.persistence import RunRecord, get_run, insert_run, list_runs


def _result(**kw) -> ResultMessage:
    base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                num_turns=2, session_id="sess-1", total_cost_usd=0.01, result="final")
    base.update(kw)
    return ResultMessage(**base)


def _drive_with(monkeypatch, messages):
    import bran.runner as runner_mod
    from bran.runner import _drive

    async def fake_query(prompt, options):
        for m in messages:
            yield m

    monkeypatch.setattr(runner_mod, "query", fake_query)
    record = RunRecord.new(agent="orchestrator", task="delegate", source="chat")
    record.status = "running"
    insert_run(record)

    async def go():
        async for _ in _drive(record, "delegate", options=None):
            pass

    try:
        asyncio.run(go())
    except Exception:
        pass
    return record


def test_delegation_becomes_a_child_run(monkeypatch):
    parent = _drive_with(monkeypatch, [
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Agent",
                                  input={"subagent_type": "research", "prompt": "find the BoE rate"})],
            model="m",
        ),
        AssistantMessage(content=[TextBlock("inner: searching…")], model="m", parent_tool_use_id="t1"),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "t1",
                              "content": "It is 4%.\nagentId: a1b2c3", "is_error": False}]),
        AssistantMessage(content=[TextBlock("The rate is 4%.")], model="m"),
        _result(),
    ])
    assert parent.status == "completed"
    children = list_runs(parent_run_id=parent.id)
    assert len(children) == 1
    c = children[0]
    assert c.source == "subagent" and c.agent == "research" and c.task == "find the BoE rate"
    assert c.status == "completed" and c.result.startswith("It is 4%")
    assert c.metadata["agent_id"] == "a1b2c3"
    assert c.metadata["depth"] == 1 and c.metadata["tool_use_id"] == "t1"
    assert c.duration_ms is not None and c.ended_at
    # The subagent's inner prose lives on the child, not the parent.
    assert [e["text"] for e in c.metadata["events"] if e["type"] == "text"] == ["inner: searching…"]
    parent_texts = [e["text"] for e in parent.metadata["events"] if e["type"] == "text"]
    assert parent_texts == ["The rate is 4%."]
    assert parent.metadata["subagents_invoked"] == ["research"]
    assert parent.metadata["subagent_runs"] == {"t1": c.id}


def test_child_fails_when_parent_ends_first(monkeypatch):
    parent = _drive_with(monkeypatch, [
        AssistantMessage(
            content=[ToolUseBlock(id="t2", name="Task", input={"subagent_type": "summariser", "prompt": "x"})],
            model="m",
        ),
        # stream ends with no tool result and no ResultMessage
    ])
    assert parent.status == "failed"
    (c,) = list_runs(parent_run_id=parent.id)
    assert c.status == "failed" and "Parent run ended" in (c.error or "")


def test_error_tool_result_fails_the_child(monkeypatch):
    parent = _drive_with(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="t3", name="Agent", input={"prompt": "p"})], model="m"),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "t3", "content": "boom", "is_error": True}]),
        _result(),
    ])
    (c,) = list_runs(parent_run_id=parent.id)
    assert c.agent == "general-purpose" and c.status == "failed" and c.error == "boom"
    assert get_run(c.id).parent_run_id == parent.id


def test_runs_endpoint_filters_by_parent(monkeypatch):
    parent = _drive_with(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="t4", name="Agent", input={"subagent_type": "research", "prompt": "q"})], model="m"),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "t4", "content": "ok", "is_error": False}]),
        _result(),
    ])
    with TestClient(build_app(enable_scheduler=False)) as c:
        rows = c.get(f"/spa/runs?parent_run_id={parent.id}").json()
    assert [r["source"] for r in rows] == ["subagent"]
    assert rows[0]["parent_run_id"] == parent.id
