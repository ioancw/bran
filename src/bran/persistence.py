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
    metadata        TEXT,
    project_id      TEXT,
    source          TEXT,
    schedule_id     TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_agent      ON runs(agent);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
-- NB: idx_runs_project is created in the migration, AFTER project_id is added,
-- so init_db() doesn't reference a column that legacy tables lack yet.

CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    agent       TEXT NOT NULL,
    task        TEXT NOT NULL,
    cron        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    project_id  TEXT,
    run_at      TEXT
);

-- Chat metadata. Each row corresponds to a single conversation in the web UI.
-- `id` mirrors the SDK session_id so we can resume via the SDK's `resume`
-- option; `agent` locks each chat to one agent so we can route messages
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

-- Discrete project memory entries (Cowork-style): a list of pinned facts/rules,
-- distinct from `projects.instructions` (the free-form brief). Each is one
-- declarative note; the system prompt assembles brief + entries.
CREATE TABLE IF NOT EXISTS project_memories (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_memories ON project_memories(project_id);
"""

# Reentrant so the lazy bootstrap (_ensure_ready -> init_db -> _conn) can take
# the lock again on the same thread without deadlocking.
_lock = threading.RLock()


def utcnow_iso() -> str:
    """UTC now as an ISO-8601 string. Shared timestamp source across modules."""
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
    started_at: str = field(default_factory=utcnow_iso)
    ended_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # The project this run belongs to, or None for a standalone run (e.g. a
    # Runner not attached to any project). Chat-originated runs carry the chat's
    # project; autonomous Runners are standalone unless explicitly attached.
    project_id: str | None = None
    # How this run was triggered: "chat" (interactive turn) | "runner"
    # (scheduled) | "spawn" (background spawn_agent) | "manual" (one-shot/API).
    # Lets the project Activity feed show autonomous work and hide chat turns.
    source: str = "manual"
    # The runner (schedule) this run belongs to, if any — set for scheduled
    # fires and for "run now" launched from a runner, so a runner's history is
    # exact rather than approximated by agent. None for chats/spawns/ad-hoc.
    schedule_id: str | None = None

    @staticmethod
    def new(
        agent: str,
        task: str,
        parent_run_id: str | None = None,
        project_id: str | None = None,
        source: str = "manual",
        schedule_id: str | None = None,
    ) -> "RunRecord":
        return RunRecord(
            id=str(uuid.uuid4()),
            agent=agent,
            task=task,
            status="pending",
            parent_run_id=parent_run_id,
            project_id=project_id,
            source=source,
            schedule_id=schedule_id,
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
            self.project_id,
            self.source,
            self.schedule_id,
        )

    @staticmethod
    def from_row(row: sqlite3.Row) -> "RunRecord":
        keys = row.keys()
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
            # None = standalone; absent only on legacy rows read mid-migration.
            project_id=(row["project_id"] if "project_id" in keys else None),
            source=(row["source"] if "source" in keys and row["source"] else "manual"),
            schedule_id=(row["schedule_id"] if "schedule_id" in keys else None),
        )


@dataclass
class ScheduleRecord:
    id: str
    name: str
    agent: str
    task: str
    cron: str  # 5-field cron expression
    enabled: bool = True
    created_at: str = field(default_factory=utcnow_iso)
    # Optional project this Runner is attached to (for context/visibility).
    # None = standalone automation, the default — Runners don't belong to projects.
    project_id: str | None = None
    # One-shot trigger: an ISO-8601 datetime to fire once, then self-disable.
    # None = a recurring cron schedule (the `cron` field). Mutually exclusive.
    run_at: str | None = None

    @staticmethod
    def new(
        name: str, agent: str, task: str, cron: str,
        project_id: str | None = None, run_at: str | None = None,
    ) -> "ScheduleRecord":
        return ScheduleRecord(
            id=str(uuid.uuid4()), name=name, agent=agent, task=task, cron=cron,
            project_id=project_id, run_at=run_at,
        )


_initialized = False


def _ensure_ready() -> None:
    """Create dirs + schema + run migrations once, lazily on first DB access.

    Keeps `import bran` free of filesystem side effects (the schema used to be
    built at import time). The flag is set before the nested init/_conn calls so
    they don't recurse; on failure it's reset so a later call can retry.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True
    try:
        SETTINGS.ensure_dirs()
        init_db()
        _migrate_chats_add_project_id()
        _migrate_runs_schedules_add_project_id()
        _drop_inbox()
    except Exception:
        _initialized = False
        raise


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    _ensure_ready()
    SETTINGS.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SETTINGS.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL lets readers and a writer coexist without blocking each other, and
    # busy_timeout makes a contended write wait-and-retry rather than fail
    # immediately with "database is locked" — both matter once concurrent
    # background runs (scheduler + spawned + HTTP) hammer the same row.
    # journal_mode is persistent (set once on the file); the rest are
    # per-connection, so we apply them on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
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
             total_cost_usd, num_turns, duration_ms, started_at, ended_at, metadata,
             project_id, source, schedule_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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


def reconcile_interrupted_runs() -> int:
    """Mark any run still 'pending'/'running' as failed. Called once at server
    startup: after a restart these are orphans — the asyncio task that was
    driving them died with the previous process, so nothing will ever finish
    them and they'd otherwise show in-progress (yellow) forever. Returns the
    number reconciled."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE runs SET status = 'failed', "
            "error = COALESCE(error, 'Interrupted — the server restarted before this run finished.'), "
            "ended_at = COALESCE(ended_at, ?) "
            "WHERE status IN ('pending', 'running')",
            (utcnow_iso(),),
        )
        return cur.rowcount


def list_runs(
    agent: str | None = None, limit: int = 50, status: str | None = None,
    project_id: str | None = None, exclude_chats: bool = False,
    schedule_id: str | None = None,
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
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if schedule_id:
        clauses.append("schedule_id = ?")
        params.append(schedule_id)
    if exclude_chats:
        # Activity = autonomous/background runs (runner fires, spawns, manual) —
        # never interactive chat turns. Keyed on the explicit `source`, so it's
        # reliable even for failed/orphaned chat runs the old session_id
        # heuristic leaked.
        clauses.append("(source IS NULL OR source != 'chat')")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [RunRecord.from_row(r) for r in rows]


def _row_to_schedule(r: sqlite3.Row) -> ScheduleRecord:
    keys = r.keys()
    return ScheduleRecord(
        id=r["id"],
        name=r["name"],
        agent=r["agent"],
        task=r["task"],
        cron=r["cron"],
        enabled=bool(r["enabled"]),
        created_at=r["created_at"],
        project_id=(r["project_id"] if "project_id" in keys else None),
        run_at=(r["run_at"] if "run_at" in keys else None),
    )


def insert_schedule(record: ScheduleRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO schedules
               (id, name, agent, task, cron, enabled, created_at, project_id, run_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.name,
                record.agent,
                record.task,
                record.cron,
                1 if record.enabled else 0,
                record.created_at,
                record.project_id,
                record.run_at,
            ),
        )


def list_schedules(project_id: str | None = None) -> list[ScheduleRecord]:
    sql = "SELECT * FROM schedules"
    params: list[Any] = []
    if project_id:
        sql += " WHERE project_id = ?"
        params.append(project_id)
    sql += " ORDER BY name"
    with _lock, _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_schedule(r) for r in rows]


def delete_schedule(name: str) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM schedules WHERE name = ?", (name,))
        return cur.rowcount > 0


def get_schedule(name: str) -> ScheduleRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM schedules WHERE name = ?", (name,)
        ).fetchone()
    return _row_to_schedule(row) if row else None


def update_schedule(
    name: str, *, agent: str, task: str, cron: str, run_at: str | None,
) -> ScheduleRecord | None:
    """Edit an existing runner's agent/task/trigger in place (keyed by name).

    `name`, `project_id`, and `enabled` are preserved — name is the stable
    identifier (the APScheduler job id + how the user refers to it), and pausing
    is a separate action. Returns the fresh record, or None if no such runner.
    Callers running `bran serve` should re-register the live job afterwards.
    """
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE schedules SET agent = ?, task = ?, cron = ?, run_at = ? WHERE name = ?",
            (agent, task, cron, run_at, name),
        )
        if cur.rowcount == 0:
            return None
    return get_schedule(name)


def set_schedule_enabled(name: str, enabled: bool) -> ScheduleRecord | None:
    """Pause/resume a runner. Updates the row and returns the fresh record (or
    None if no such schedule). Callers in `bran serve` should also (un)register
    the live APScheduler job — see scheduler.register_schedule/unregister_schedule."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE schedules SET enabled = ? WHERE name = ?",
            (1 if enabled else 0, name),
        )
        if cur.rowcount == 0:
            return None
    return get_schedule(name)


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
    # Optional project. None = a loose chat (shown in the global Chat/Recents
    # view); set = belongs to that project. No "Inbox" — loose is the default.
    project_id: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


def _row_to_chat(r: sqlite3.Row) -> ChatRecord:
    return ChatRecord(
        id=r["id"], title=r["title"], agent=r["agent"],
        project_id=(r["project_id"] if "project_id" in r.keys() else None),
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
            (utcnow_iso(), chat_id),
        )


def list_chats(limit: int = 100, project_id: str | None = None) -> list[ChatRecord]:
    """Return chats sorted by most-recently updated.

    `project_id` given → chats in that project. `project_id` None → **loose**
    chats (no project) — the global Chat/Recents view. (There's no "all chats"
    view: a chat is either loose or in exactly one project.)
    """
    sql = "SELECT * FROM chats"
    params: list[Any] = []
    if project_id:
        sql += " WHERE project_id = ?"
        params.append(project_id)
    else:
        sql += " WHERE project_id IS NULL"
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
            (project_id, utcnow_iso(), chat_id),
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
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

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
    record.updated_at = utcnow_iso()
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
    """Return all projects, most-recently-updated first."""
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [_row_to_project(r) for r in rows]


def delete_project(project_id: str) -> bool:
    """Remove a project + its memory entries. Its chats become loose (project_id
    NULL) rather than being deleted, so no conversation history is lost."""
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE chats SET project_id = NULL WHERE project_id = ?", (project_id,)
        )
        conn.execute("DELETE FROM project_memories WHERE project_id = ?", (project_id,))
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Project memory entries — discrete pinned facts (distinct from the brief)
# ---------------------------------------------------------------------------

@dataclass
class ProjectMemory:
    id: str
    project_id: str
    text: str
    created_at: str = field(default_factory=utcnow_iso)

    @staticmethod
    def new(project_id: str, text: str) -> "ProjectMemory":
        return ProjectMemory(id=str(uuid.uuid4()), project_id=project_id, text=text)


def add_project_memory(project_id: str, text: str) -> ProjectMemory:
    rec = ProjectMemory.new(project_id=project_id, text=text.strip())
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO project_memories (id, project_id, text, created_at) VALUES (?, ?, ?, ?)",
            (rec.id, rec.project_id, rec.text, rec.created_at),
        )
    return rec


def list_project_memories(project_id: str) -> list[ProjectMemory]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM project_memories WHERE project_id = ? ORDER BY created_at",
            (project_id,),
        ).fetchall()
    return [
        ProjectMemory(id=r["id"], project_id=r["project_id"], text=r["text"], created_at=r["created_at"])
        for r in rows
    ]


def delete_project_memory(entry_id: str) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM project_memories WHERE id = ?", (entry_id,))
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

def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not any(c["name"] == column for c in cols):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_chats_add_project_id() -> None:
    """Add project_id column to chats table if it doesn't already exist."""
    with _lock, _conn() as conn:
        _add_column_if_missing(conn, "chats", "project_id", "TEXT")


def _migrate_runs_schedules_add_project_id() -> None:
    """Add the (optional) project_id column to runs + schedules.

    Attachment is optional — `project_id IS NULL` means loose/standalone.
    """
    with _lock, _conn() as conn:
        _add_column_if_missing(conn, "runs", "project_id", "TEXT")
        _add_column_if_missing(conn, "schedules", "project_id", "TEXT")
        # `source` classifies how a run was triggered (chat/runner/spawn/manual).
        # Backfill legacy rows: a run whose session_id is a chat is a chat turn;
        # everything else we can only call "manual" in hindsight.
        _add_column_if_missing(conn, "runs", "source", "TEXT")
        conn.execute(
            "UPDATE runs SET source = 'chat' "
            "WHERE source IS NULL AND session_id IN (SELECT id FROM chats)"
        )
        conn.execute("UPDATE runs SET source = 'manual' WHERE source IS NULL")
        # schedule_id links a run to the runner that produced it (exact history);
        # run_at marks a one-shot ("once") schedule. Both nullable, no backfill.
        _add_column_if_missing(conn, "runs", "schedule_id", "TEXT")
        _add_column_if_missing(conn, "schedules", "run_at", "TEXT")
        # Safe to index now that the column exists on both fresh and legacy DBs.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id)")


def _drop_inbox() -> None:
    """Inbox was a fudge — a catch-all 'project' for unfiled chats. We dropped
    it: a chat/run/schedule with project_id NULL is simply *loose* (not in any
    project). Convert any legacy 'inbox' rows back to NULL and remove the
    well-known Inbox project. Idempotent — a no-op once nothing references it."""
    with _lock, _conn() as conn:
        for table in ("chats", "runs", "schedules"):
            conn.execute(f"UPDATE {table} SET project_id = NULL WHERE project_id = 'inbox'")
        conn.execute("DELETE FROM projects WHERE id = 'inbox'")


# Schema + migrations run lazily on first DB access via _ensure_ready(), so
# importing this module has no filesystem side effects.
