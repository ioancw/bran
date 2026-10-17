"""Chat history and run transcripts survive Claude Code pruning its session
files: every run keeps a trimmed copy of its events, and the history/transcript
endpoints rebuild from the database when the JSONL file is gone."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from bran.api import build_app
from bran.persistence import RunRecord, insert_run, utcnow_iso
from bran.web.events import STORED_RESULT_CHARS, compact_for_storage, events_from_runs


@pytest.fixture(scope="module")
def client():
    with TestClient(build_app(enable_scheduler=False)) as c:
        yield c


def _turn(sid: str, task: str, *, result=None, status="completed", error=None,
          started: str | None = None, metadata=None) -> RunRecord:
    rec = RunRecord.new(agent="orchestrator", task=task)
    rec.session_id = sid
    rec.source = "chat"
    rec.status = status
    rec.result = result
    rec.error = error
    rec.started_at = started or utcnow_iso()
    rec.ended_at = utcnow_iso()
    rec.metadata = dict(metadata or {})
    return rec


# ---------------------------------------------------------------------------
# Pure reconstruction
# ---------------------------------------------------------------------------

def test_events_from_runs_rebuilds_prompt_and_reply_in_order():
    sid = "s-" + uuid.uuid4().hex
    newer = _turn(sid, "second", result="reply two", started="2026-07-02T10:00:00+00:00")
    older = _turn(sid, "first", result="reply one", started="2026-07-01T10:00:00+00:00")
    events = events_from_runs([newer, older])  # deliberately unsorted
    assert [e["type"] for e in events] == ["user", "text", "user", "text"]
    assert [e["text"] for e in events] == ["first", "reply one", "second", "reply two"]
    assert events[0]["ts"] == "2026-07-01T10:00:00+00:00"


def test_events_from_runs_surfaces_failures_and_cancellations():
    sid = "s-" + uuid.uuid4().hex
    failed = _turn(sid, "boom", status="failed", error="ProcessError: no CLI",
                   started="2026-07-02T00:00:00+00:00")
    cancelled = _turn(sid, "stop", status="cancelled", error="Run cancelled",
                      started="2026-07-03T00:00:00+00:00")
    events = events_from_runs([failed, cancelled])
    assert events[1] == {"type": "error", "message": "ProcessError: no CLI"}
    assert events[3] == {"type": "error", "message": "Run cancelled"}


def test_events_from_runs_prefers_the_stored_rich_events():
    sid = "s-" + uuid.uuid4().hex
    stored = [
        {"type": "session", "session_id": sid},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}, "tool_id": "t1"},
        {"type": "tool_result", "tool_id": "t1", "text": "…", "is_error": False},
        {"type": "text", "text": "rich reply", "ts": "2026-07-01T10:00:05+00:00"},
        {"type": "result", "num_turns": 2, "total_cost_usd": 0.01, "session_id": sid},
    ]
    rec = _turn(sid, "hi", result="rich reply", metadata={"events": stored})
    events = events_from_runs([rec])
    # Prompt first, then the stored events minus the session marker — the
    # final `result` text is NOT duplicated from rec.result.
    assert events[0]["type"] == "user"
    assert [e["type"] for e in events[1:]] == ["tool_use", "tool_result", "text", "result"]


def test_compact_for_storage_trims_only_long_tool_results():
    short = {"type": "tool_result", "tool_id": "t", "text": "ok", "is_error": False}
    assert compact_for_storage(short) is short
    long = {**short, "text": "x" * (STORED_RESULT_CHARS + 50)}
    out = compact_for_storage(long)
    assert out["text"].endswith("[… result truncated …]")
    assert len(out["text"]) < len(long["text"])
    assert compact_for_storage({"type": "text", "text": "y" * 20_000})["text"] == "y" * 20_000


# ---------------------------------------------------------------------------
# The run driver keeps its events on the row
# ---------------------------------------------------------------------------

def test_drive_stores_events_on_the_run(monkeypatch):
    import bran.runner as runner_mod
    from bran.runner import _drive

    class TextBlock:
        def __init__(self, text): self.text = text

    class AssistantMessage:
        def __init__(self, content): self.content = content

    class ResultMessage:
        def __init__(self):
            self.num_turns = 1
            self.total_cost_usd = 0.001
            self.session_id = "sess-1"
            self.subtype = "success"
            self.is_error = False
            self.result = "hello there"

    async def fake_query(prompt, options):
        yield AssistantMessage([TextBlock("hello there")])
        yield ResultMessage()

    monkeypatch.setattr(runner_mod, "query", fake_query)
    record = RunRecord.new(agent="research", task="say hi")
    record.status = "running"
    insert_run(record)

    async def go():
        async for _ in _drive(record, "say hi", options=None):
            pass

    asyncio.run(go())
    kept = record.metadata["events"]
    assert [e["type"] for e in kept] == ["text", "result"]
    assert kept[0]["text"] == "hello there"
    assert "ts" in kept[0]
    assert "events_truncated" not in record.metadata


# ---------------------------------------------------------------------------
# Endpoints fall back when the session file is missing
# ---------------------------------------------------------------------------

def test_chat_history_rebuilds_from_runs_when_file_is_gone(client):
    sid = "gone-" + uuid.uuid4().hex  # no ~/.claude/projects/*/<sid>.jsonl exists
    insert_run(_turn(sid, "what is the fx vol smile?", result="A smile is…",
                     started="2026-06-01T09:00:00+00:00"))
    insert_run(_turn(sid, "and the skew?", result="Skew is…",
                     started="2026-06-01T09:05:00+00:00"))
    r = client.get(f"/spa/chats/{sid}/history")
    assert r.status_code == 200
    events = r.json()["events"]
    assert [e["type"] for e in events] == ["user", "text", "user", "text"]
    assert events[2]["text"] == "and the skew?"


def test_chat_history_is_empty_for_an_unknown_session(client):
    r = client.get("/spa/chats/never-existed/history")
    assert r.status_code == 200
    assert r.json()["events"] == []


def test_run_transcript_falls_back_to_stored_events(client):
    rec = RunRecord.new(agent="research", task="dig")
    rec.session_id = "gone-" + uuid.uuid4().hex
    rec.status = "completed"
    rec.result = "found it"
    rec.metadata = {"events": [{"type": "text", "text": "found it"}]}
    insert_run(rec)
    r = client.get(f"/spa/runs/{rec.id}/transcript")
    assert r.status_code == 200
    assert [e["type"] for e in r.json()["events"]] == ["user", "text"]
