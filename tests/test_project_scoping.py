"""project_id on runs + schedules — the workspace-spine data model."""

from __future__ import annotations

import sqlite3
import uuid

from bran.persistence import (
    ChatRecord,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    _add_column_if_missing,
    _conn,
    add_project_memory,
    delete_project,
    delete_project_memory,
    get_chat,
    get_run,
    get_schedule,
    insert_project,
    insert_run,
    insert_schedule,
    list_chats,
    list_project_memories,
    list_runs,
    list_schedules,
    upsert_chat,
)


def _chat(project_id=None) -> ChatRecord:
    c = ChatRecord(id=f"sess-{uuid.uuid4().hex}", title="t", agent="orchestrator", project_id=project_id)
    upsert_chat(c)
    return c


def test_chat_defaults_to_loose():
    assert ChatRecord(id="x", title="t", agent="orchestrator").project_id is None


def test_loose_chats_vs_project_chats():
    pid = _project()
    loose = _chat(project_id=None)
    inproj = _chat(project_id=pid)
    # list_chats() with no project = loose chats only
    loose_ids = {c.id for c in list_chats(project_id=None)}
    assert loose.id in loose_ids and inproj.id not in loose_ids
    # scoped to the project = only its chats
    proj_ids = {c.id for c in list_chats(project_id=pid)}
    assert inproj.id in proj_ids and loose.id not in proj_ids


def test_deleting_project_makes_its_chats_loose():
    pid = _project()
    c = _chat(project_id=pid)
    assert delete_project(pid) is True
    assert get_chat(c.id).project_id is None  # loose, not deleted


def test_run_source_defaults_to_manual_and_roundtrips():
    rec = RunRecord.new(agent="research", task="t")
    assert rec.source == "manual"
    rec2 = RunRecord.new(agent="research", task="t", source="runner")
    insert_run(rec2)
    assert get_run(rec2.id).source == "runner"


def test_project_memory_crud_and_cascade():
    pid = _project()
    m1 = add_project_memory(pid, "fact one")
    add_project_memory(pid, "fact two")
    assert [m.text for m in list_project_memories(pid)] == ["fact one", "fact two"]
    assert delete_project_memory(m1.id) is True
    assert [m.text for m in list_project_memories(pid)] == ["fact two"]
    # cascade: deleting the project removes its memory entries
    delete_project(pid)
    assert list_project_memories(pid) == []


def test_activity_excludes_chat_turn_runs():
    # A project's "Activity" = autonomous/background runs, classified by `source`
    # (not a session_id heuristic), so failed/orphaned chat runs don't leak.
    pid = _project()
    turn = RunRecord.new(agent="orchestrator", task="hi", project_id=pid, source="chat")
    insert_run(turn)
    bg = RunRecord.new(agent="research", task="bg", project_id=pid, source="runner")
    insert_run(bg)

    activity = {r.id for r in list_runs(project_id=pid, exclude_chats=True)}
    assert bg.id in activity and turn.id not in activity
    everything = {r.id for r in list_runs(project_id=pid)}
    assert turn.id in everything and bg.id in everything


def test_legacy_migration_adds_column_then_indexes():
    """Reproduces the migration-order bug: a pre-existing table without
    project_id must get the column added BEFORE an index references it."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE runs (id TEXT)")  # legacy schema, no project_id
    _add_column_if_missing(conn, "runs", "project_id", "TEXT")
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(runs)").fetchall()}
    assert "project_id" in cols
    _add_column_if_missing(conn, "runs", "project_id", "TEXT")  # idempotent, no raise
    # Indexing the column must now succeed (this is what blew up against a real DB).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id)")
    conn.close()


def _project() -> str:
    p = ProjectRecord.new(name=f"proj-{uuid.uuid4().hex[:6]}")
    insert_project(p)
    return p.id


def test_runs_and_schedules_have_project_id_columns():
    with _conn() as conn:
        run_cols = {c["name"] for c in conn.execute("PRAGMA table_info(runs)").fetchall()}
        sch_cols = {c["name"] for c in conn.execute("PRAGMA table_info(schedules)").fetchall()}
    assert "project_id" in run_cols
    assert "project_id" in sch_cols


def test_run_defaults_to_standalone():
    # A run is standalone (no project) unless one is supplied — only chat-origin
    # and project-attached runs carry a project_id.
    rec = RunRecord.new(agent="research", task="t")
    assert rec.project_id is None
    insert_run(rec)
    assert get_run(rec.id).project_id is None


def test_run_project_roundtrip_and_filter():
    pid = _project()
    rec = RunRecord.new(agent="research", task="t", project_id=pid)
    insert_run(rec)
    # round-trips
    assert get_run(rec.id).project_id == pid
    # filterable by project
    ids = {r.id for r in list_runs(project_id=pid, limit=50)}
    assert rec.id in ids
    # and excluded from a different project's view
    other = _project()
    assert rec.id not in {r.id for r in list_runs(project_id=other, limit=50)}


def test_schedule_project_roundtrip_and_filter():
    pid = _project()
    name = f"sched-{uuid.uuid4().hex[:6]}"
    rec = ScheduleRecord.new(name=name, agent="research", task="t", cron="0 8 * * *", project_id=pid)
    insert_schedule(rec)
    assert get_schedule(name).project_id == pid
    assert name in {s.name for s in list_schedules(project_id=pid)}
    assert name not in {s.name for s in list_schedules(project_id=_project())}


def test_schedule_defaults_to_standalone():
    # A Runner is a standalone automation by default — not a project member.
    name = f"sched-{uuid.uuid4().hex[:6]}"
    insert_schedule(ScheduleRecord.new(name=name, agent="research", task="t", cron="0 8 * * *"))
    assert get_schedule(name).project_id is None
