"""project_id on runs + schedules — the workspace-spine data model."""

from __future__ import annotations

import sqlite3
import uuid

from bran.persistence import (
    INBOX_PROJECT_ID,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    _add_column_if_missing,
    _conn,
    get_run,
    get_schedule,
    insert_project,
    insert_run,
    insert_schedule,
    list_runs,
    list_schedules,
)


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
