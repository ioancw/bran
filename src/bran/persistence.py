"""SQLite-backed store for agent runs and schedules.

Two tables:
- runs: every invocation of an agent (status, result, session_id, cost, ...)
- schedules: cron-style triggers managed by APScheduler
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from bran.config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    agent           TEXT NOT NULL,
    task            TEXT NOT NULL,
    status          TEXT NOT NULL,
    session_id      TEXT,
    parent_run_id   TEXT,
    result          TEXT,
    error           TEXT,
    total_cost_usd  REAL,
    num_turns       INTEGER,
    duration_ms     INTEGER,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    metadata        TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_agent      ON runs(agent);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);

CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    agent       TEXT NOT NULL,
    task        TEXT NOT NULL,
    cron        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
"""

_lock = threading.Lock()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    id: str
    agent: str
    task: str
    status: str  # pending | running | completed | failed
    session_id: str | None = None
    parent_run_id: str | None = None
    result: str | None = None
    error: str | None = None
    total_cost_usd: float | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    started_at: str = field(default_factory=_utcnow_iso)
    ended_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(agent: str, task: str, parent_run_id: str | None = None) -> "RunRecord":
        return RunRecord(
            id=str(uuid.uuid4()),
            agent=agent,
            task=task,
            status="pending",
            parent_run_id=parent_run_id,
        )

    def to_row(self) -> tuple:
        return (
            self.id,
            self.agent,
            self.task,
            self.status,
            self.session_id,
            self.parent_run_id,
            self.result,
            self.error,
            self.total_cost_usd,
            self.num_turns,
            self.duration_ms,
            self.started_at,
            self.ended_at,
            json.dumps(self.metadata),
        )

    @staticmethod
    def from_row(row: sqlite3.Row) -> "RunRecord":
        return RunRecord(
            id=row["id"],
            agent=row["agent"],
            task=row["task"],
            status=row["status"],
            session_id=row["session_id"],
            parent_run_id=row["parent_run_id"],
            result=row["result"],
            error=row["error"],
            total_cost_usd=row["total_cost_usd"],
            num_turns=row["num_turns"],
            duration_ms=row["duration_ms"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )


@dataclass
class ScheduleRecord:
    id: str
    name: str
    agent: str
    task: str
    cron: str  # 5-field cron expression
    enabled: bool = True
    created_at: str = field(default_factory=_utcnow_iso)

    @staticmethod
    def new(name: str, agent: str, task: str, cron: str) -> "ScheduleRecord":
        return ScheduleRecord(
            id=str(uuid.uuid4()), name=name, agent=agent, task=task, cron=cron
        )


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    SETTINGS.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SETTINGS.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _lock, _conn() as conn:
        conn.executescript(SCHEMA)


def insert_run(record: RunRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO runs
            (id, agent, task, status, session_id, parent_run_id, result, error,
             total_cost_usd, num_turns, duration_ms, started_at, ended_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            record.to_row(),
        )


def update_run(record: RunRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """UPDATE runs SET
                status = ?, session_id = ?, result = ?, error = ?,
                total_cost_usd = ?, num_turns = ?, duration_ms = ?,
                ended_at = ?, metadata = ?
               WHERE id = ?""",
            (
                record.status,
                record.session_id,
                record.result,
                record.error,
                record.total_cost_usd,
                record.num_turns,
                record.duration_ms,
                record.ended_at,
                json.dumps(record.metadata),
                record.id,
            ),
        )


def get_run(run_id: str) -> RunRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return RunRecord.from_row(row) if row else None


def list_runs(
    agent: str | None = None, limit: int = 50, status: str | None = None
) -> list[RunRecord]:
    sql = "SELECT * FROM runs"
    clauses: list[str] = []
    params: list[Any] = []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [RunRecord.from_row(r) for r in rows]


def insert_schedule(record: ScheduleRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO schedules (id, name, agent, task, cron, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.name,
                record.agent,
                record.task,
                record.cron,
                1 if record.enabled else 0,
                record.created_at,
            ),
        )


def list_schedules() -> list[ScheduleRecord]:
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT * FROM schedules ORDER BY name").fetchall()
    return [
        ScheduleRecord(
            id=r["id"],
            name=r["name"],
            agent=r["agent"],
            task=r["task"],
            cron=r["cron"],
            enabled=bool(r["enabled"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


def delete_schedule(name: str) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM schedules WHERE name = ?", (name,))
        return cur.rowcount > 0


def get_schedule(name: str) -> ScheduleRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM schedules WHERE name = ?", (name,)
        ).fetchone()
    if not row:
        return None
    return ScheduleRecord(
        id=row["id"],
        name=row["name"],
        agent=row["agent"],
        task=row["task"],
        cron=row["cron"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
    )


def run_to_dict(r: RunRecord) -> dict[str, Any]:
    return asdict(r)


# Ensure schema exists on import — cheap, idempotent.
init_db()
