"""Continuing a chat whose SDK transcript was pruned: bran starts a fresh SDK
session under the same id, seeds it with the restored conversation, and keeps
serving that chat's history from the database afterwards."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import bran.web.spa_routes.chats as spa
from bran.api import build_app
from bran.persistence import ChatRecord, RunRecord, get_setting, insert_run, upsert_chat, utcnow_iso
from bran.web.events import restored_context


def _turn(sid: str, task: str, result: str, started: str) -> None:
    r = RunRecord.new(agent="orchestrator", task=task, source="chat")
    r.session_id = sid
    r.status = "completed"
    r.result = result
    r.started_at = started
    r.ended_at = utcnow_iso()
    insert_run(r)


def test_restored_context_keeps_the_latest_turns():
    events = [
        {"type": "user", "text": "first question"},
        {"type": "text", "text": "first answer"},
        {"type": "tool_use", "name": "Read", "input": {}, "tool_id": "t"},
        {"type": "user", "text": "second question"},
        {"type": "text", "text": "x" * 50},
    ]
    out = restored_context(events)
    assert out.startswith("## Earlier in this conversation (restored)")
    assert "User: first question" in out and "You: first answer" in out
    assert "Read" not in out  # tool calls aren't part of the restored dialogue
    short = restored_context(events, limit=60)
    assert "[… earlier turns omitted …]" in short and "first question" not in short
    assert restored_context([{"type": "tool_use"}]) == ""


def _capture(monkeypatch):
    seen: dict = {}

    async def fake_stream(agent, prompt, *, resume_session=None, session_id=None, append_system=None, **kw):
        seen.update(agent=agent, prompt=prompt, resume=resume_session,
                    session_id=session_id, append_system=append_system)
        return
        yield  # pragma: no cover

    monkeypatch.setattr(spa, "stream_agent", fake_stream)
    return seen


def test_pruned_transcript_starts_fresh_session_with_same_id(monkeypatch):
    sid = str(uuid.uuid4())
    upsert_chat(ChatRecord(id=sid, title="t", agent="orchestrator"))
    _turn(sid, "what is the fx vol smile?", "A smile is the shape of implied vol across strikes.",
          "2026-06-01T09:00:00+00:00")
    monkeypatch.setattr(spa, "find_session_file", lambda _sid: None)  # pruned
    seen = _capture(monkeypatch)

    with TestClient(build_app(enable_scheduler=False)) as c:
        r = c.post("/spa/chat/stream", data={"prompt": "and the skew?", "session_id": sid})
        assert r.status_code == 200
        assert seen["resume"] is None and seen["session_id"] == sid
        assert "restored" in seen["append_system"]
        assert "User: what is the fx vol smile?" in seen["append_system"]
        assert "You: A smile is the shape" in seen["append_system"]
        assert get_setting(spa.DB_HISTORY_KEY + sid) == "1"

        # From now on history comes from the database even once a new SDK
        # file exists (it would only hold the turns since the restart).
        monkeypatch.setattr(spa, "find_session_file", lambda _sid: Path("/tmp/not-read.jsonl"))
        monkeypatch.setattr(spa, "parse_transcript", lambda _p: [])
        events = c.get(f"/spa/chats/{sid}/history").json()["events"]
        assert [e["type"] for e in events] == ["user", "text"]
        assert events[0]["text"] == "what is the fx vol smile?"


def test_intact_transcript_resumes_normally(monkeypatch):
    sid = str(uuid.uuid4())
    upsert_chat(ChatRecord(id=sid, title="t", agent="orchestrator"))
    monkeypatch.setattr(spa, "find_session_file", lambda _sid: Path("/tmp/present.jsonl"))
    seen = _capture(monkeypatch)
    with TestClient(build_app(enable_scheduler=False)) as c:
        c.post("/spa/chat/stream", data={"prompt": "hi", "session_id": sid})
    assert seen["resume"] == sid and seen["session_id"] is None
    assert not (seen["append_system"] or "")  # nothing restored, nothing injected
    assert get_setting(spa.DB_HISTORY_KEY + sid, "") == ""


# --- a turn outlives the page that started it ---------------------------------

def test_a_turn_carries_on_when_its_reader_leaves(monkeypatch):
    """Closing the page stops the reading, not the turn; the chat can find the
    turn meanwhile."""
    from types import SimpleNamespace

    from bran.background import is_running

    sid = "sess-" + uuid.uuid4().hex
    upsert_chat(ChatRecord(id=sid, title="t", agent="orchestrator"))
    rec = RunRecord.new(agent="orchestrator", task="keep going", source="chat")
    rec.session_id = sid
    rec.status = "running"
    insert_run(rec)
    progress: list[str] = []

    async def fake_stream(agent, prompt, *, on_start=None, **kw):
        on_start(rec)
        for step in ("one", "two", "three"):
            await asyncio.sleep(0.01)
            progress.append(step)
            yield SimpleNamespace(parent_tool_use_id=None, step=step)

    monkeypatch.setattr(spa, "stream_agent", fake_stream)
    monkeypatch.setattr(spa, "events_from_message", lambda m: [{"type": "text", "text": m.step}])
    monkeypatch.setattr(spa, "find_session_file", lambda _sid: Path("/tmp/present.jsonl"))

    async def leave_early() -> None:
        resp = await spa.chat_stream(prompt="keep going", session_id=sid)
        frames = resp.body_iterator
        first = await frames.__anext__()
        assert json.loads(first[len("data: "):]) == {"type": "run", "run_id": rec.id}
        assert is_running(rec.id)
        assert spa.chat_live(sid) == {"run_id": rec.id, "task": "keep going"}
        await frames.aclose()  # the page goes away
        for _ in range(300):
            if not is_running(rec.id):
                break
            await asyncio.sleep(0.01)
        assert spa.chat_live(sid) == {"run_id": None, "task": ""}

    asyncio.run(leave_early())
    assert progress == ["one", "two", "three"]


def test_a_turn_that_has_not_begun_goes_with_its_reader(monkeypatch):
    began: list[str] = []

    async def fake_stream(agent, prompt, *, on_start=None, **kw):
        await asyncio.sleep(0.05)
        began.append("ran")
        return
        yield  # pragma: no cover

    monkeypatch.setattr(spa, "stream_agent", fake_stream)

    async def leave_at_once() -> None:
        resp = await spa.chat_stream(prompt="never mind")
        reading = asyncio.ensure_future(resp.body_iterator.__anext__())
        await asyncio.sleep(0.01)
        reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)
        await asyncio.sleep(0.1)

    asyncio.run(leave_at_once())
    assert began == []


def test_stopping_a_turn_cancels_its_run(monkeypatch):
    """Stop is an explicit cancel of the run the stream named."""
    from types import SimpleNamespace

    from bran.background import cancel_background

    seen: list[str] = []

    async def fake_stream(agent, prompt, *, on_start=None, **kw):
        on_start(SimpleNamespace(id="run-to-stop"))
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise
        yield  # pragma: no cover

    monkeypatch.setattr(spa, "stream_agent", fake_stream)

    async def stop_it() -> list[str]:
        resp = await spa.chat_stream(prompt="long job")
        frames = [f async for f in _until_stopped(resp.body_iterator)]
        return frames

    async def _until_stopped(body):
        async for frame in body:
            yield frame
            if '"run"' in frame:
                assert cancel_background("run-to-stop") == 1

    frames = asyncio.run(stop_it())
    assert seen == ["cancelled"]
    assert json.loads(frames[-1][len("data: "):]) == {"type": "done"}
