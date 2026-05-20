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

-- Chat metadata. Each row corresponds to a single conversation in the web UI.
-- `id` mirrors the SDK session_id so we can resume via the SDK's `resume`
-- option; `agent` locks each chat to one persona so we can route messages
-- consistently regardless of which orchestrator was chatting last.
-- `project_id` groups chats into a Project (Claude-Cowork style); migration
-- below ensures every chat row has one (defaulting to the auto-Inbox).
CREATE TABLE IF NOT EXISTS chats (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    agent       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated_at DESC);

-- Projects: a named container for related chats. `instructions` is the
-- always-on memory blob appended to every chat's system prompt in this
-- project. Files attached to the project (PDFs, CSVs, etc.) live in a
-- per-project folder under SETTINGS.bran_home / "projects" / id / files/
-- and are referenced from the system prompt so the agent can read them
-- on demand.
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    instructions  TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC);
"""

INBOX_PROJECT_ID = "inbox"   # well-known id so callers can reference it

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


# ---------------------------------------------------------------------------
# Chats (web UI conversations)
# ---------------------------------------------------------------------------

@dataclass
class ChatRecord:
    id: str                 # SDK session_id — used as primary key
    title: str              # truncated first user prompt
    agent: str              # which agent this chat is locked to
    project_id: str = INBOX_PROJECT_ID  # which project this chat belongs to
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)


def _row_to_chat(r: sqlite3.Row) -> ChatRecord:
    # project_id may be absent in legacy rows that pre-date the migration;
    # `r["project_id"]` will raise IndexError on those, so we use dict-style.
    pid = r["project_id"] if "project_id" in r.keys() else None
    return ChatRecord(
        id=r["id"], title=r["title"], agent=r["agent"],
        project_id=pid or INBOX_PROJECT_ID,
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


def upsert_chat(record: ChatRecord) -> None:
    """Insert a new chat row or update the existing one (matching by id)."""
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO chats (id, title, agent, project_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 updated_at = excluded.updated_at""",
            (record.id, record.title, record.agent, record.project_id,
             record.created_at, record.updated_at),
        )


def touch_chat(chat_id: str) -> None:
    """Bump updated_at on an existing chat (e.g. on a new message)."""
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE chats SET updated_at = ? WHERE id = ?",
            (_utcnow_iso(), chat_id),
        )


def list_chats(limit: int = 100, project_id: str | None = None) -> list[ChatRecord]:
    """Return chats sorted by most-recently updated.

    If `project_id` is given, only chats in that project are returned.
    """
    sql = "SELECT * FROM chats"
    params: list[Any] = []
    if project_id is not None:
        sql += " WHERE project_id = ?"
        params.append(project_id)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_chat(r) for r in rows]


def get_chat(chat_id: str) -> ChatRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return _row_to_chat(row) if row else None


def delete_chat(chat_id: str) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return cur.rowcount > 0


def move_chat_to_project(chat_id: str, project_id: str) -> bool:
    """Reassign a chat to a different project. Used by the UI's drag/drop."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE chats SET project_id = ?, updated_at = ? WHERE id = ?",
            (project_id, _utcnow_iso(), chat_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Projects (Claude-Cowork-style containers for related chats + memory)
# ---------------------------------------------------------------------------

@dataclass
class ProjectRecord:
    id: str
    name: str
    description: str = ""
    instructions: str = ""    # the inline memory blob — appended to chats' system prompt
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)

    @staticmethod
    def new(name: str, description: str = "", instructions: str = "") -> "ProjectRecord":
        return ProjectRecord(
            id=str(uuid.uuid4()),
            name=name, description=description, instructions=instructions,
        )


def _row_to_project(r: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        id=r["id"], name=r["name"],
        description=r["description"] or "", instructions=r["instructions"] or "",
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


def insert_project(record: ProjectRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO projects (id, name, description, instructions, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (record.id, record.name, record.description, record.instructions,
             record.created_at, record.updated_at),
        )


def update_project(record: ProjectRecord) -> None:
    record.updated_at = _utcnow_iso()
    with _lock, _conn() as conn:
        conn.execute(
            """UPDATE projects SET
                 name = ?, description = ?, instructions = ?, updated_at = ?
               WHERE id = ?""",
            (record.name, record.description, record.instructions,
             record.updated_at, record.id),
        )


def get_project(project_id: str) -> ProjectRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return _row_to_project(row) if row else None


def list_projects() -> list[ProjectRecord]:
    """Return all projects, Inbox last (so user-created ones come first)."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM projects
               ORDER BY (id = ?) ASC, updated_at DESC""",
            (INBOX_PROJECT_ID,),
        ).fetchall()
    return [_row_to_project(r) for r in rows]


def delete_project(project_id: str) -> bool:
    """Remove a project. Refuses to delete the Inbox. Chats inside the project
    are reassigned to Inbox rather than deleted, so no conversation history is lost."""
    if project_id == INBOX_PROJECT_ID:
        return False
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE chats SET project_id = ? WHERE project_id = ?",
            (INBOX_PROJECT_ID, project_id),
        )
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0


def count_chats_per_project() -> dict[str, int]:
    """{project_id: n_chats} — used by the /projects grid for card stats."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT project_id, COUNT(*) AS n FROM chats GROUP BY project_id"
        ).fetchall()
    return {r["project_id"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# Migrations — run after init_db() creates the bare tables.
# ---------------------------------------------------------------------------

def _migrate_chats_add_project_id() -> None:
    """Add project_id column to chats table if it doesn't already exist."""
    with _lock, _conn() as conn:
        cols = conn.execute("PRAGMA table_info(chats)").fetchall()
        if not any(c["name"] == "project_id" for c in cols):
            conn.execute("ALTER TABLE chats ADD COLUMN project_id TEXT")


def _ensure_inbox_project() -> None:
    """Auto-create the Inbox project + retro-assign any orphan chats."""
    with _lock, _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM projects WHERE id = ?", (INBOX_PROJECT_ID,)
        ).fetchone()
        if not existing:
            now = _utcnow_iso()
            conn.execute(
                """INSERT INTO projects (id, name, description, instructions, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (INBOX_PROJECT_ID, "Inbox",
                 "Casual one-off chats that don't belong to a specific project.",
                 "", now, now),
            )
        # Backfill orphan chats. After the ALTER TABLE above they all have NULL
        # project_id; this assigns Inbox so the rest of the app can assume a
        # project_id is always set.
        conn.execute(
            "UPDATE chats SET project_id = ? WHERE project_id IS NULL",
            (INBOX_PROJECT_ID,),
        )


# Ensure schema + migrations run on import — cheap, idempotent.
init_db()
_migrate_chats_add_project_id()
_ensure_inbox_project()
