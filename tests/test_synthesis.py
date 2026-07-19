"""Headless fan-in (bran.synthesis): batch readiness guards, the race-free
one-winner claim, and the synthesis run itself (runner stubbed)."""

from __future__ import annotations

import asyncio
import uuid

import pytest

import bran.runner as runner
from bran.persistence import (
    ChatRecord,
    RunRecord,
    claim_fanout_synthesis,
    get_run,
    insert_run,
    upsert_chat,
    utcnow_iso,
)
from bran.synthesis import maybe_synthesise


def _chat_turn(status: str = "completed", with_chat: bool = True) -> RunRecord:
    """A finished chat-turn run with (optionally) its ChatRecord."""
    sid = f"sess-{uuid.uuid4().hex}"
    r = RunRecord.new(agent="orchestrator", task="fan out please", source="chat")
    r.status = status
    r.session_id = sid
    r.ended_at = utcnow_iso()
    insert_run(r)
    if with_chat:
        upsert_chat(ChatRecord(id=sid, title="t", agent="orchestrator"))
    return r


def _spawn(parent: RunRecord, status: str = "completed", inline: bool = False) -> RunRecord:
    s = RunRecord.new(agent="research", task="subtask", source="spawn",
                      parent_run_id=parent.id)
    s.status = status
    if status != "pending":
        s.ended_at = utcnow_iso()
    if inline:
        s.metadata["inline"] = True
    insert_run(s)
    return s


@pytest.fixture()
def fake_run_agent(monkeypatch):
    """Stub runner.run_agent, capturing calls."""
    calls: list[dict] = []

    async def _fake(agent, task, **kwargs):
        calls.append({"agent": agent, "task": task, **kwargs})
        rec = RunRecord.new(agent=agent, task=task, source=kwargs.get("source", "manual"))
        rec.status = "completed"
        rec.result = "combined"
        return rec

    monkeypatch.setattr(runner, "run_agent", _fake)
    return calls


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

def test_claim_has_exactly_one_winner():
    parent = _chat_turn()
    assert claim_fanout_synthesis(parent.id) is True
    assert claim_fanout_synthesis(parent.id) is False  # already claimed
    assert get_run(parent.id).metadata["fanout_synthesis"] == "claimed"


def test_claim_preserves_existing_metadata():
    parent = _chat_turn()
    rec = get_run(parent.id)
    rec.metadata["something"] = "kept"
    from bran.persistence import update_run

    update_run(rec)
    assert claim_fanout_synthesis(parent.id) is True
    rec = get_run(parent.id)
    assert rec.metadata["something"] == "kept"
    assert rec.metadata["fanout_synthesis"] == "claimed"


# ---------------------------------------------------------------------------
# Readiness guards (no claim burned when the batch isn't ready)
# ---------------------------------------------------------------------------

def test_no_synthesis_while_parent_running(fake_run_agent):
    parent = _chat_turn(status="running")
    _spawn(parent), _spawn(parent)
    assert asyncio.run(maybe_synthesise(parent.id)) is None
    assert not fake_run_agent
    assert claim_fanout_synthesis(parent.id)  # claim untouched by the check


def test_no_synthesis_while_spawns_in_flight(fake_run_agent):
    parent = _chat_turn()
    _spawn(parent), _spawn(parent, status="pending")
    assert asyncio.run(maybe_synthesise(parent.id)) is None
    assert not fake_run_agent
    assert claim_fanout_synthesis(parent.id)


def test_single_spawn_is_not_a_fanout(fake_run_agent):
    parent = _chat_turn()
    _spawn(parent)
    assert asyncio.run(maybe_synthesise(parent.id)) is None
    assert not fake_run_agent


def test_inline_spawns_do_not_count(fake_run_agent):
    parent = _chat_turn()
    _spawn(parent), _spawn(parent, inline=True)
    assert asyncio.run(maybe_synthesise(parent.id)) is None
    assert not fake_run_agent


def test_non_chat_parent_skipped(fake_run_agent):
    parent = RunRecord.new(agent="orchestrator", task="t", source="runner")
    parent.status = "completed"
    insert_run(parent)
    _spawn(parent), _spawn(parent)
    assert asyncio.run(maybe_synthesise(parent.id)) is None
    assert not fake_run_agent


def test_all_failed_batch_claims_but_skips(fake_run_agent):
    parent = _chat_turn()
    _spawn(parent, status="failed"), _spawn(parent, status="failed")
    assert asyncio.run(maybe_synthesise(parent.id)) is None
    assert not fake_run_agent
    # The claim IS consumed — an all-failed batch can never become mergeable.
    assert claim_fanout_synthesis(parent.id) is False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_synthesis_fires_once_with_run_ids(fake_run_agent):
    parent = _chat_turn()
    s1 = _spawn(parent)
    s2 = _spawn(parent, status="failed")
    s3 = _spawn(parent)

    out = asyncio.run(maybe_synthesise(parent.id))
    assert out is not None
    assert len(fake_run_agent) == 1
    call = fake_run_agent[0]
    assert call["source"] == "synthesis"
    assert call["resume_session"] == parent.session_id
    assert call["parent_run_id"] == parent.id
    for s in (s1, s2, s3):
        assert s.id in call["task"]
    assert "failed" in call["task"]  # the gap is flagged, not hidden

    # A second check (racing sibling / duplicate trigger) is a no-op.
    assert asyncio.run(maybe_synthesise(parent.id)) is None
    assert len(fake_run_agent) == 1


# ---------------------------------------------------------------------------
# Notification suppression for batch members
# ---------------------------------------------------------------------------

def test_batch_spawns_do_not_push_but_failures_and_loners_do():
    from bran.synthesis import suppresses_notification

    parent = _chat_turn()
    ok = _spawn(parent)
    failed = _spawn(parent, status="failed")
    assert suppresses_notification(ok) is True        # synthesis covers it
    assert suppresses_notification(failed) is False   # failures always ping

    lone_parent = _chat_turn()
    lone = _spawn(lone_parent)
    assert suppresses_notification(lone) is False     # not a fan-out — push

    runner_parent = RunRecord.new(agent="orchestrator", task="t", source="runner")
    runner_parent.status = "completed"
    insert_run(runner_parent)
    from_runner = _spawn(runner_parent)
    _spawn(runner_parent)
    assert suppresses_notification(from_runner) is False  # no synthesis coming
