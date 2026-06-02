"""Persistence round-trips against a temp SQLite DB (BRAN_HOME → tmp, see conftest)."""

from __future__ import annotations

import uuid

from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    _conn,
    delete_schedule,
    get_run,
    get_schedule,
    insert_run,
    insert_schedule,
    list_runs,
    list_schedules,
    update_run,
)


def _unique_agent() -> str:
    """A per-test agent name so filtered queries don't see other tests' rows."""
    return f"agent-{uuid.uuid4().hex[:8]}"


def test_wal_mode_enabled():
    with _conn() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_run_record_roundtrip_preserves_metadata():
    rec = RunRecord.new(agent="research", task="hello")
    rec.metadata = {"subagents_invoked": ["research", "summariser"]}
    insert_run(rec)

    fetched = get_run(rec.id)
    assert fetched is not None
    assert fetched.agent == "research"
    assert fetched.task == "hello"
    assert fetched.status == "pending"
    assert fetched.metadata == {"subagents_invoked": ["research", "summariser"]}


def test_update_run_persists_status_and_cost():
    rec = RunRecord.new(agent="research", task="t")
    insert_run(rec)

    rec.status = "completed"
    rec.result = "the answer"
    rec.total_cost_usd = 0.1234
    rec.num_turns = 3
    update_run(rec)

    fetched = get_run(rec.id)
    assert fetched is not None
    assert fetched.status == "completed"
    assert fetched.result == "the answer"
    assert fetched.total_cost_usd == 0.1234
    assert fetched.num_turns == 3


def test_list_runs_filters_and_limit():
    agent = _unique_agent()
    ids = []
    for i in range(3):
        rec = RunRecord.new(agent=agent, task=f"task {i}")
        rec.started_at = f"2026-05-27T10:0{i}:00+00:00"
        rec.status = "completed" if i == 0 else "failed"
        insert_run(rec)
        ids.append(rec.id)

    by_agent = list_runs(agent=agent, limit=50)
    assert {r.id for r in by_agent} == set(ids)
    # Ordered most-recent first by started_at.
    assert [r.started_at for r in by_agent] == sorted(
        [r.started_at for r in by_agent], reverse=True
    )

    failed = list_runs(agent=agent, status="failed", limit=50)
    assert {r.status for r in failed} == {"failed"}
    assert len(failed) == 2

    assert len(list_runs(agent=agent, limit=1)) == 1


def test_schedule_crud():
    name = f"sched-{uuid.uuid4().hex[:8]}"
    rec = ScheduleRecord.new(name=name, agent="research", task="t", cron="0 8 * * *")
    insert_schedule(rec)

    got = get_schedule(name)
    assert got is not None
    assert got.cron == "0 8 * * *"
    assert got.enabled is True
    assert name in {s.name for s in list_schedules()}

    assert delete_schedule(name) is True
    assert get_schedule(name) is None
    assert delete_schedule(name) is False  # already gone
