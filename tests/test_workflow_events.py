"""Workflows in the fleet: a Workflow tool call becomes a child run, each agent
the script spawns (task_started) becomes a child of that workflow run, and
task notifications close them. Plus the per-agent budget from the agent file."""

from __future__ import annotations

import asyncio
import json

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)

from bran.agents import build_options_for, get_agent
from bran.persistence import RunRecord, insert_run, list_runs

SCRIPT = "export const meta = { name: 'fact-check', description: 'Check a document', phases: [] }\nreturn 1"


def _result() -> ResultMessage:
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                         num_turns=2, session_id="s", total_cost_usd=0.5, result="done")


def _drive(monkeypatch, messages):
    import bran.runner as runner_mod
    from bran.runner import _drive

    async def fake_query(prompt, options):
        for m in messages:
            yield m

    monkeypatch.setattr(runner_mod, "query", fake_query)
    rec = RunRecord.new(agent="orchestrator", task="check it", source="chat")
    rec.status = "running"
    insert_run(rec)

    async def go():
        async for _ in _drive(rec, "check it", options=None):
            pass

    try:
        asyncio.run(go())
    except Exception:
        pass
    return rec


def test_workflow_call_and_its_agents_become_child_runs(monkeypatch):
    parent = _drive(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="wf1", name="Workflow", input={"script": SCRIPT, "args": {"path": "/x.md"}})], model="m"),
        TaskStartedMessage(subtype="task_started", data={}, uuid="u", session_id="s", task_id="t-a", description="extract claims",
                           tool_use_id="wf1", task_type="workflow_agent"),
        TaskStartedMessage(subtype="task_started", data={}, uuid="u", session_id="s", task_id="t-b", description="verify 1",
                           tool_use_id="wf1", task_type="workflow_agent"),
        TaskNotificationMessage(subtype="task_notification", data={}, uuid="u", session_id="s", output_file="", task_id="t-a", status="completed",
                                summary="3 claims", tool_use_id="wf1"),
        TaskNotificationMessage(subtype="task_notification", data={}, uuid="u", session_id="s", output_file="", task_id="t-b", status="failed",
                                summary="search error", tool_use_id="wf1"),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "wf1",
                              "content": '{"counts": {"supported": 2}}', "is_error": False}]),
        AssistantMessage(content=[TextBlock("Here is the report.")], model="m"),
        _result(),
    ])
    assert parent.status == "completed"
    (wf,) = list_runs(parent_run_id=parent.id)
    assert wf.source == "subagent" and wf.agent == "workflow:fact-check" and wf.task == "Check a document"
    assert wf.status == "completed" and wf.metadata.get("workflow") is True
    assert wf.result.startswith('{"counts"')
    agents = {r.task: r for r in list_runs(parent_run_id=wf.id)}
    assert set(agents) == {"extract claims", "verify 1"}
    assert agents["extract claims"].status == "completed" and agents["extract claims"].result == "3 claims"
    assert agents["verify 1"].status == "failed" and "failed" in (agents["verify 1"].error or "")
    assert agents["verify 1"].agent == "workflow-agent"
    assert agents["extract claims"].metadata["depth"] == 2


def test_background_agent_tool_task_does_not_duplicate_the_delegation(monkeypatch):
    parent = _drive(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="a1", name="Agent", input={"subagent_type": "research", "prompt": "p"})], model="m"),
        TaskStartedMessage(subtype="task_started", data={}, uuid="u", session_id="s", task_id="t-1", description="research: p",
                           tool_use_id="a1", task_type="local_agent"),
        TaskNotificationMessage(subtype="task_notification", data={}, uuid="u", session_id="s", output_file="", task_id="t-1", status="completed",
                                summary="partial", tool_use_id="a1"),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "a1", "content": "the real answer", "is_error": False}]),
        _result(),
    ])
    kids = list_runs(parent_run_id=parent.id)
    assert len(kids) == 1 and kids[0].agent == "research"
    assert kids[0].result == "the real answer"  # the tool result, not the task summary
    assert list_runs(parent_run_id=kids[0].id) == []


def test_parent_death_fails_open_workflow_agents(monkeypatch):
    parent = _drive(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="wf2", name="Workflow", input={"name": "fact-check", "args": {"path": "/y"}})], model="m"),
        TaskStartedMessage(subtype="task_started", data={}, uuid="u", session_id="s", task_id="t-z", description="verify 3",
                           tool_use_id="wf2", task_type="workflow_agent"),
        # stream ends: no notifications, no result
    ])
    assert parent.status == "failed"
    (wf,) = list_runs(parent_run_id=parent.id)
    assert wf.status == "failed" and wf.agent == "workflow:fact-check" and "args" in wf.task
    (a,) = list_runs(parent_run_id=wf.id)
    assert a.status == "failed" and "Parent run ended" in (a.error or "")


def test_task_updated_terminal_status_closes_the_agent(monkeypatch):
    parent = _drive(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="wf3", name="Workflow", input={"name": "fact-check"})], model="m"),
        TaskStartedMessage(subtype="task_started", data={}, uuid="u", session_id="s", task_id="t-k",
                           description="verify 2", tool_use_id="wf3", task_type="workflow_agent"),
        TaskUpdatedMessage(subtype="task_updated", data={}, task_id="t-k", patch={"status": "running"}),
        TaskUpdatedMessage(subtype="task_updated", data={}, task_id="t-k", patch={"status": "killed"}),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "wf3", "content": "{}", "is_error": False}]),
        _result(),
    ])
    (wf,) = list_runs(parent_run_id=parent.id)
    (a,) = list_runs(parent_run_id=wf.id)
    assert a.status == "failed" and "killed" in (a.error or "")


def test_orchestrator_has_workflow_tool_and_its_own_budget():
    o = get_agent("orchestrator")
    assert "Workflow" in o.tools and o.budget_usd == 15
    assert build_options_for(o).max_budget_usd == 15
    assert build_options_for(get_agent("summariser")).max_budget_usd != 15


def _started(task_id, description, tool_use_id, task_type="local_workflow"):
    return TaskStartedMessage(subtype="task_started", data={}, uuid="u", session_id="s", task_id=task_id,
                              description=description, tool_use_id=tool_use_id, task_type=task_type)


def test_background_workflow_closes_on_its_task_notification_and_imports_the_journal(monkeypatch, tmp_path):
    tdir = tmp_path / "wf_abc"
    tdir.mkdir()
    (tdir / "journal.jsonl").write_text("\n".join([
        json.dumps({"type": "launched"}),
        json.dumps({"type": "started", "agentId": "a1", "label": "extract claims", "phase": "Extract"}),
        json.dumps({"type": "result", "agentId": "a1", "result": {"claims": [1, 2]}}),
        json.dumps({"type": "started", "agentId": "a2", "label": "verify 1", "phase": "Verify"}),
        json.dumps({"type": "result", "agentId": "a2", "result": None}),
        json.dumps({"type": "started", "agentId": "a3", "label": "verify 2", "phase": "Verify"}),
    ]) + "\n", encoding="utf-8")
    (tdir / "agent-a2.meta.json").write_text(json.dumps({"agentType": "research", "description": "verify 1"}))
    (tdir / "agent-a1.jsonl").write_text(
        json.dumps({"timestamp": "2026-10-18T17:20:55.000Z"}) + "\n" + json.dumps({"timestamp": "2026-10-18T17:21:05.000Z"}) + "\n")
    out = tmp_path / "task.output"
    out.write_text(json.dumps({"summary": "Check a document", "agentCount": 3, "logs": ["2 claims"],
                               "result": {"counts": {"supported": 1}, "report": "# Report\n\nAll good."}}))
    boilerplate = (
        "Workflow started.\nTranscript dir: " + str(tdir) + "\nScript file: /x.js\nRun ID: wf_abc\n"
        "You will be notified when it completes. Use /workflows to watch live progress."
    )
    parent = _drive(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="wf9", name="Workflow", input={"name": "fact-check", "args": {"path": "/d.md"}})], model="m"),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "wf9", "content": boilerplate, "is_error": False}]),
        _started("t-wf", "Check a document", "wf9"),
        AssistantMessage(content=[TextBlock("Running in the background.")], model="m"),
        _result(),
        TaskNotificationMessage(subtype="task_notification", data={}, uuid="u", session_id="s", task_id="t-wf",
                                status="completed", summary="done", tool_use_id="wf9", output_file=str(out),
                                usage={"total_tokens": 1234, "tool_uses": 7, "duration_ms": 70000}),
        AssistantMessage(content=[TextBlock("# Report")], model="m"),
        _result(),
    ])
    assert parent.status == "completed"
    (wf,) = list_runs(parent_run_id=parent.id)
    assert wf.agent == "workflow:fact-check" and wf.task == "Check a document"
    assert wf.status == "completed" and wf.result == "# Report\n\nAll good."
    assert wf.metadata["transcript_dir"] == str(tdir) and wf.metadata["workflow_run_id"] == "wf_abc"
    assert wf.metadata["workflow_summary"]["agentCount"] == 3 and wf.metadata["task_usage"]["total_tokens"] == 1234
    agents = {r.metadata["label"]: r for r in list_runs(parent_run_id=wf.id)}
    assert set(agents) == {"extract claims", "verify 1", "verify 2"}
    a1, a2, a3 = agents["extract claims"], agents["verify 1"], agents["verify 2"]
    assert a1.status == "completed" and json.loads(a1.result) == {"claims": [1, 2]} and a1.task == "Extract: extract claims"
    assert a1.started_at == "2026-10-18T17:20:55.000Z" and a1.duration_ms == 10000
    assert a1.agent == "workflow-agent" and a2.agent == "research"
    assert a2.status == "failed" and "returned nothing" in a2.error
    assert a3.status == "failed" and "ended before" in a3.error
    assert all(r.metadata["depth"] == 2 for r in agents.values())


def test_workflow_tool_error_fails_the_workflow_row(monkeypatch):
    parent = _drive(monkeypatch, [
        AssistantMessage(content=[ToolUseBlock(id="wf8", name="Workflow", input={"script": "garbage"})], model="m"),
        UserMessage(content=[{"type": "tool_result", "tool_use_id": "wf8", "content": "SyntaxError: bad script", "is_error": True}]),
        _result(),
    ])
    (wf,) = list_runs(parent_run_id=parent.id)
    assert wf.status == "failed" and "SyntaxError" in wf.error and wf.agent == "workflow:workflow"
