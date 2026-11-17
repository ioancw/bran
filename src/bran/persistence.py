"""SQLite-backed store: runs, schedules (runners), chats, projects.

`_TABLES` + `_INDEXES` are the CURRENT full shape — what a fresh database is
created with. An existing database is brought up to it by the numbered
`_MIGRATIONS`, gated on `PRAGMA user_version`, so each step runs exactly once.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from bran.config import SETTINGS

_TABLES = """
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
    schedule_id     TEXT,
    actor           TEXT
);

-- A run's stored transcript (the unified events, trimmed + capped by the
-- runner). Its own table, not a key inside runs.metadata: one run's events
-- can reach 1.5MB, and every list query used to read, parse and ship them.
-- Only the transcript / history / sources paths load these.
CREATE TABLE IF NOT EXISTS run_events (
    run_id  TEXT PRIMARY KEY,
    events  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    agent       TEXT NOT NULL,
    task        TEXT NOT NULL,
    cron        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    project_id  TEXT,
    run_at      TEXT,
    verify      INTEGER NOT NULL DEFAULT 0,
    delta       INTEGER NOT NULL DEFAULT 0,
    alert       TEXT NOT NULL DEFAULT '',
    -- The teammate this automation works for (NULL = a plain automation).
    teammate_id TEXT
);

-- Teammates: an agent given a name and a standing job. Each has ONE chat it
-- is steered from (`chat_id`, the SDK session; NULL until the first message)
-- and any number of automations (schedules.teammate_id) that do its repeating
-- work. `brief` is the standing job description: it rides in the system
-- prompt of both the chat and the scheduled runs.
CREATE TABLE IF NOT EXISTS teammates (
    id          TEXT PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    agent       TEXT NOT NULL,
    brief       TEXT NOT NULL DEFAULT '',
    chat_id     TEXT,
    project_id  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Files a run produced (captured from its Write/Edit tool calls). First-class
-- rows rather than a list inside runs.metadata, so the schema is explicit and
-- queryable. (run_id, path) is naturally unique — re-recording is a no-op.
CREATE TABLE IF NOT EXISTS artifacts (
    run_id      TEXT NOT NULL,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, path)
);

-- Chat metadata. Each row corresponds to a single conversation in the web UI.
-- `id` mirrors the SDK session_id so we can resume via the SDK's `resume`
-- option; `agent` locks each chat to one agent so we can route messages
-- consistently regardless of which orchestrator was chatting last.
-- `project_id` groups chats into a Project (NULL = a loose chat);
-- `archived_at` hides a chat from Recents without deleting it.
CREATE TABLE IF NOT EXISTS chats (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    agent       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    project_id  TEXT,
    archived_at TEXT
);

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
    -- Optional working folder (absolute path) agents in this project may read
    -- AND write — the write-confinement hook admits it as an extra root.
    work_dir      TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    -- The auto-written brief (see bran.project_summary) and when it was written.
    summary       TEXT NOT NULL DEFAULT '',
    summary_at    TEXT
);

-- Discrete project memory entries (Cowork-style): a list of pinned facts/rules,
-- distinct from `projects.instructions` (the free-form brief). Each is one
-- declarative note; the system prompt assembles brief + entries.
CREATE TABLE IF NOT EXISTS project_memories (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- App-level key-value settings (single-user). Currently holds
-- 'user_instructions' — the global "About me" blob appended to every agent
-- run's system prompt (bran's equivalent of Cowork's global instructions).
CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL DEFAULT ''
);

-- Per-output reading state (single-user): whether you starred a run's result
-- and when you last read it. Keyed by run id; a missing row means
-- unread + unstarred. Kept out of the runs table so the hot write path
-- (insert/update_run) is untouched and this stays purely a UI concern.
CREATE TABLE IF NOT EXISTS output_state (
    run_id   TEXT PRIMARY KEY,
    starred  INTEGER NOT NULL DEFAULT 0,
    read_at  TEXT
);

-- Thumbs up/down on a chat answer, with the question and answer it was given
-- for: the raw material of an evaluation set. `answer_key` is the UI's
-- fingerprint of the answer text, so a rating finds its bubble again after a
-- reload.
CREATE TABLE IF NOT EXISTS answer_feedback (
    session_id  TEXT NOT NULL,
    answer_key  TEXT NOT NULL,
    rating      INTEGER NOT NULL,
    prompt      TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL DEFAULT '',
    agent       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, answer_key)
);
"""

# Created after the tables AND after any migration has added the columns they
# reference — an index on a column a legacy table lacks would fail.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_runs_agent      ON runs(agent);
CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_runs_project    ON runs(project_id);
-- The chat rail polls runs by session every few seconds; child-run and
-- runner-history lookups filter on the other two.
CREATE INDEX IF NOT EXISTS idx_runs_session    ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_runs_parent     ON runs(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_runs_schedule   ON runs(schedule_id, started_at);
-- find_subagent_run: one lookup per delegation event during replay.
CREATE INDEX IF NOT EXISTS idx_runs_tool_use
    ON runs(json_extract(metadata, '$.tool_use_id')) WHERE source = 'subagent';
CREATE INDEX IF NOT EXISTS idx_chats_updated   ON chats(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chats_project   ON chats(project_id);
CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_project_memories ON project_memories(project_id);
"""

# Reentrant: store functions call each other while holding it (insert_run ->
# save_run_events, delete_chat -> delete_runs).
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
    # Who triggered the run, when known. Set for the bearer-token /api surface
    # (the token's name, see BRAN_API_TOKENS); None for the local single-user
    # surfaces (SPA, CLI, REPL, scheduler).
    actor: str | None = None

    @staticmethod
    def new(
        agent: str,
        task: str,
        parent_run_id: str | None = None,
        project_id: str | None = None,
        source: str = "manual",
        schedule_id: str | None = None,
        actor: str | None = None,
    ) -> RunRecord:
        return RunRecord(
            id=str(uuid.uuid4()),
            agent=agent,
            task=task,
            status="pending",
            parent_run_id=parent_run_id,
            project_id=project_id,
            source=source,
            schedule_id=schedule_id,
            actor=actor,
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
            _metadata_json(self),
            self.project_id,
            self.source,
            self.schedule_id,
            self.actor,
        )

    @staticmethod
    def from_row(row: sqlite3.Row) -> RunRecord:
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
            project_id=row["project_id"],  # None = standalone
            source=row["source"] or "manual",
            schedule_id=row["schedule_id"],
            actor=row["actor"],
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
    # Quality controls for the scheduled output (see scheduler._fire):
    # verify — an evaluator pass reviews each completed run's output against the
    #          task; on a failed verdict the run is retried ONCE with the
    #          reviewer's feedback (cookbook evaluator-optimizer pattern).
    # delta  — feed the previous completed run's report into the next fire so
    #          the agent reports what's NEW/CHANGED rather than restating state.
    # alert  — natural-language significance bar for a sensing runner (empty =
    #          off). When set, the agent judges each run against it and leads
    #          the report with the ALERT marker only when the bar is crossed;
    #          notifiers escalate marked runs (see bran.notify).
    verify: bool = False
    delta: bool = False
    alert: str = ""
    # The teammate this automation works for (None = a plain automation). Its
    # runs carry the teammate's brief and what the user last told it.
    teammate_id: str | None = None

    @staticmethod
    def new(
        name: str, agent: str, task: str, cron: str,
        project_id: str | None = None, run_at: str | None = None,
        verify: bool = False, delta: bool = False, alert: str = "",
        teammate_id: str | None = None,
    ) -> ScheduleRecord:
        return ScheduleRecord(
            id=str(uuid.uuid4()), name=name, agent=agent, task=task, cron=cron,
            project_id=project_id, run_at=run_at, verify=verify, delta=delta,
            alert=alert, teammate_id=teammate_id,
        )


_initialized = False


def _ensure_ready() -> None:
    """Create dirs + schema + run migrations once, lazily on first DB access.

    Keeps `import bran` free of filesystem side effects (the schema used to be
    built at import time). Double-checked under the lock so two threads racing
    on first use can't both migrate.
    """
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        SETTINGS.ensure_dirs()
        conn = _connect()
        try:
            _bring_up_to_date(conn)
        finally:
            conn.close()
        _initialized = True


def _connect() -> sqlite3.Connection:
    SETTINGS.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SETTINGS.db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL lets readers and a writer coexist without blocking each other, and
    # busy_timeout makes a contended write wait-and-retry rather than fail
    # immediately with "database is locked" — both matter once concurrent
    # background runs (scheduler + spawned + HTTP) hammer the same row.
    # journal_mode is persistent (set once on the file); the rest are
    # per-connection.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# One long-lived connection per thread (the event loop's thread, plus each
# threadpool worker that serves a read endpoint), instead of a connect + three
# PRAGMAs on every call. Keyed by path so a changed SETTINGS.db_path is honoured.
_local = threading.local()


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    _ensure_ready()
    path = str(SETTINGS.db_path)
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        if conn is not None:
            conn.close()
        conn = _local.conn = _connect()
        _local.path = path
    yield conn


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    """All-or-nothing for a multi-statement write (connections autocommit)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _metadata_json(record: RunRecord) -> str:
    """The run's metadata for the row. A caller that still carries the stored
    transcript under metadata["events"] has it moved to run_events."""
    meta = record.metadata
    if "events" in meta:
        events = meta["events"]
        meta = {k: v for k, v in meta.items() if k != "events"}
        if events:
            save_run_events(record.id, events)
    return json.dumps(meta)


def save_run_events(run_id: str, events: list[dict[str, Any]]) -> None:
    """Store (replace) a run's transcript events."""
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO run_events (run_id, events) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET events = excluded.events",
            (run_id, json.dumps(events, default=str)),
        )


def get_run_events(run_id: str) -> list[dict[str, Any]]:
    """A run's stored transcript events ([] when it kept none)."""
    return get_run_events_many([run_id]).get(run_id, [])


def get_run_events_many(run_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{run_id: events} for the runs that kept any — one query for a batch."""
    ids = [r for r in run_ids if r]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with _lock, _conn() as conn:
        rows = conn.execute(
            f"SELECT run_id, events FROM run_events WHERE run_id IN ({marks})", ids
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        try:
            events = json.loads(r["events"] or "[]")
        except ValueError:
            continue
        if isinstance(events, list) and events:
            out[r["run_id"]] = events
    return out


def insert_run(record: RunRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO runs
            (id, agent, task, status, session_id, parent_run_id, result, error,
             total_cost_usd, num_turns, duration_ms, started_at, ended_at, metadata,
             project_id, source, schedule_id, actor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            record.to_row(),
        )


def update_run(record: RunRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """UPDATE runs SET
                status = ?, task = ?, session_id = ?, result = ?, error = ?,
                total_cost_usd = ?, num_turns = ?, duration_ms = ?,
                ended_at = ?, metadata = ?
               WHERE id = ?""",
            (
                record.status,
                record.task,
                record.session_id,
                record.result,
                record.error,
                record.total_cost_usd,
                record.num_turns,
                record.duration_ms,
                record.ended_at,
                _metadata_json(record),
                record.id,
            ),
        )


def get_run(run_id: str) -> RunRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return RunRecord.from_row(row) if row else None


def list_spawns(parent_run_id: str) -> list[RunRecord]:
    """The background runs a single turn fanned out (its synthesis batch)."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE parent_run_id = ? AND source = 'spawn' "
            "ORDER BY started_at",
            (parent_run_id,),
        ).fetchall()
    return [RunRecord.from_row(r) for r in rows]


def claim_fanout_synthesis(parent_run_id: str) -> bool:
    """Atomically claim the right to synthesise a fan-out batch (at most one
    winner, ever). Sibling runs can finish near-simultaneously and each check
    the batch; the marker lives in the parent run's metadata and the UPDATE's
    WHERE clause makes the claim race-free under SQLite's single-writer lock."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE runs SET metadata = json_set("
            "  CASE WHEN metadata IS NULL OR metadata = '' THEN '{}' ELSE metadata END,"
            "  '$.fanout_synthesis', 'claimed')"
            " WHERE id = ? AND (metadata IS NULL OR metadata = ''"
            "   OR json_extract(metadata, '$.fanout_synthesis') IS NULL)",
            (parent_run_id,),
        )
        return cur.rowcount == 1


def get_run_by_session(session_id: str) -> RunRecord | None:
    """The earliest run that produced this SDK session — used to let a run's
    session be continued as a chat even though no chat row exists for it."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at LIMIT 1",
            (session_id,),
        ).fetchone()
    return RunRecord.from_row(row) if row else None


def spend_summary() -> dict[str, Any]:
    """Estimated spend (SDK client-side cost) for today / 7 days / 30 days,
    plus per-runner 30-day totals. Chat turns count — they cost money too."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    windows = {
        "today": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        "week": (now - timedelta(days=7)).isoformat(),
        "month": (now - timedelta(days=30)).isoformat(),
    }
    out: dict[str, Any] = {}
    with _lock, _conn() as conn:
        for key, since in windows.items():
            row = conn.execute(
                "SELECT COALESCE(SUM(total_cost_usd), 0) AS cost, COUNT(*) AS runs "
                "FROM runs WHERE started_at >= ?",
                (since,),
            ).fetchone()
            out[key] = {"cost": float(row["cost"] or 0), "runs": int(row["runs"] or 0)}
        per = conn.execute(
            "SELECT schedule_id, COALESCE(SUM(total_cost_usd), 0) AS cost, COUNT(*) AS runs "
            "FROM runs WHERE schedule_id IS NOT NULL AND started_at >= ? GROUP BY schedule_id",
            (windows["month"],),
        ).fetchall()
    out["runners_30d"] = {
        r["schedule_id"]: {"cost": float(r["cost"] or 0), "runs": int(r["runs"] or 0)} for r in per
    }
    return out


def find_subagent_run(tool_use_id: str) -> RunRecord | None:
    """The child run recorded for an Agent-tool call (by its tool_use id)."""
    if not tool_use_id:
        return None
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE source = 'subagent' "
            "AND json_extract(metadata, '$.tool_use_id') = ? LIMIT 1",
            (tool_use_id,),
        ).fetchone()
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
    schedule_id: str | None = None, q: str | None = None,
    session_id: str | None = None, parent_run_id: str | None = None,
    exclude_subagents: bool = False,
) -> list[RunRecord]:
    sql = "SELECT * FROM runs"
    clauses: list[str] = []
    params: list[Any] = []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    if parent_run_id:
        clauses.append("parent_run_id = ?")
        params.append(parent_run_id)
    if q and q.strip():
        # Free-text search over the readable fields. LIKE (case-insensitive for
        # ASCII) is ample at single-user scale; revisit FTS5 only if the run
        # table ever grows large enough for it to matter.
        like = f"%{q.strip()}%"
        clauses.append("(result LIKE ? OR task LIKE ? OR agent LIKE ?)")
        params.extend([like, like, like])
    if status:
        clauses.append("status = ?")
        params.append(status)
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if schedule_id:
        clauses.append("schedule_id = ?")
        params.append(schedule_id)
    if session_id:
        # Runs belonging to one chat: the chat's own turns plus the background
        # runs those turns spawned (parent chain, one level — the fan-out case).
        # Lets the chat-side Progress rail show *this* conversation's work rather
        # than every run in the project.
        clauses.append(
            "(session_id = ? OR parent_run_id IN (SELECT id FROM runs WHERE session_id = ?))"
        )
        params.extend([session_id, session_id])
    if exclude_chats:
        # Activity = autonomous/background runs (runner fires, spawns, manual) —
        # never interactive chat turns. Keyed on the explicit `source`, so it's
        # reliable even for failed/orphaned chat runs the old session_id
        # heuristic leaked.
        clauses.append("(source IS NULL OR source != 'chat')")
    if exclude_subagents:
        # The steps a run handed to sub-agents: one workflow records dozens,
        # which would crowd the runs someone actually started out of the page.
        clauses.append("(source IS NULL OR source != 'subagent')")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(max(1, int(limit)))  # a negative LIMIT means "no limit" to SQLite
    with _lock, _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [RunRecord.from_row(r) for r in rows]


def count_runs_by_source() -> dict[str, int]:
    """How many runs exist per source, chat turns left out: the true totals
    behind a page that only ever loads the newest few hundred."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(source, 'manual') AS s, count(*) AS n FROM runs"
            " WHERE source IS NULL OR source != 'chat' GROUP BY s"
        ).fetchall()
    return {r["s"]: int(r["n"]) for r in rows}


def _row_to_schedule(r: sqlite3.Row) -> ScheduleRecord:
    return ScheduleRecord(
        id=r["id"],
        name=r["name"],
        agent=r["agent"],
        task=r["task"],
        cron=r["cron"],
        enabled=bool(r["enabled"]),
        created_at=r["created_at"],
        project_id=r["project_id"],
        run_at=r["run_at"],
        verify=bool(r["verify"]),
        delta=bool(r["delta"]),
        alert=r["alert"] or "",
        teammate_id=r["teammate_id"],
    )


def insert_schedule(record: ScheduleRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO schedules
               (id, name, agent, task, cron, enabled, created_at, project_id, run_at,
                verify, delta, alert, teammate_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                1 if record.verify else 0,
                1 if record.delta else 0,
                record.alert or "",
                record.teammate_id,
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
    verify: bool | None = None, delta: bool | None = None,
    alert: str | None = None,
) -> ScheduleRecord | None:
    """Edit an existing runner's agent/task/trigger in place (keyed by name).

    `name`, `project_id`, and `enabled` are preserved — name is the stable
    identifier (the APScheduler job id + how the user refers to it), and pausing
    is a separate action. `verify`/`delta`/`alert` None = leave unchanged
    (pass alert="" to clear the significance bar). Returns the fresh record,
    or None if no such runner. Callers running `bran serve` should re-register
    the live job afterwards.
    """
    sets = ["agent = ?", "task = ?", "cron = ?", "run_at = ?"]
    params: list[Any] = [agent, task, cron, run_at]
    if verify is not None:
        sets.append("verify = ?")
        params.append(1 if verify else 0)
    if delta is not None:
        sets.append("delta = ?")
        params.append(1 if delta else 0)
    if alert is not None:
        sets.append("alert = ?")
        params.append(alert)
    params.append(name)
    with _lock, _conn() as conn:
        cur = conn.execute(
            f"UPDATE schedules SET {', '.join(sets)} WHERE name = ?", params
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
# Artifacts — files a run produced. First-class table (not runs.metadata).
# ---------------------------------------------------------------------------

def add_artifact(run_id: str, path: str) -> None:
    """Record a file a run wrote. Idempotent — (run_id, path) is the PK."""
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO artifacts (run_id, path, created_at) VALUES (?, ?, ?)",
            (run_id, path, utcnow_iso()),
        )


def list_artifacts(run_id: str) -> list[str]:
    """Paths this run wrote, in the order they were first recorded."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT path FROM artifacts WHERE run_id = ? ORDER BY created_at, path",
            (run_id,),
        ).fetchall()
    return [r["path"] for r in rows]


def list_artifacts_for_runs(run_ids: list[str]) -> dict[str, list[str]]:
    """{run_id: [paths]} for a batch of runs in one query — lets list views
    show artifacts without an N+1 per row."""
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    with _lock, _conn() as conn:
        rows = conn.execute(
            f"SELECT run_id, path FROM artifacts WHERE run_id IN ({placeholders}) "
            "ORDER BY created_at, path",
            run_ids,
        ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["run_id"], []).append(r["path"])
    return out


# ---------------------------------------------------------------------------
# Output state — per-run starred / read markers (durable, cross-browser).
# Replaces the old localStorage "seen" timestamp so reading state follows you
# between devices and the Outputs inbox can be trusted.
# ---------------------------------------------------------------------------

def set_output_starred(run_id: str, starred: bool) -> None:
    """Star/unstar a run's output. Upsert that preserves any existing read_at."""
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO output_state (run_id, starred) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET starred = excluded.starred",
            (run_id, 1 if starred else 0),
        )


def mark_outputs_read(run_ids: list[str]) -> None:
    """Stamp read_at=now on a batch of runs (upsert; preserves `starred`)."""
    if not run_ids:
        return
    now = utcnow_iso()
    with _lock, _conn() as conn:
        conn.executemany(
            "INSERT INTO output_state (run_id, read_at) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET read_at = excluded.read_at",
            [(rid, now) for rid in run_ids],
        )


def get_output_states(run_ids: list[str]) -> dict[str, dict[str, Any]]:
    """{run_id: {starred, read_at}} for a batch — attached to list responses so
    the Outputs surface renders star/read state without an N+1 per row."""
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    with _lock, _conn() as conn:
        rows = conn.execute(
            f"SELECT run_id, starred, read_at FROM output_state WHERE run_id IN ({placeholders})",
            run_ids,
        ).fetchall()
    return {
        r["run_id"]: {"starred": bool(r["starred"]), "read_at": r["read_at"]}
        for r in rows
    }


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
    # Set when the chat was archived: hidden from Recents and the project list
    # but kept, resumable, and one click from coming back.
    archived_at: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)


def _row_to_chat(r: sqlite3.Row) -> ChatRecord:
    return ChatRecord(
        id=r["id"], title=r["title"], agent=r["agent"],
        project_id=r["project_id"],
        archived_at=r["archived_at"],
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


def list_chats(
    limit: int = 100, project_id: str | None = None, archived: bool | None = False,
    exclude_teammates: bool = False,
) -> list[ChatRecord]:
    """Return chats sorted by most-recently updated.

    `project_id` given → chats in that project. `project_id` None → **all**
    chats — the global Recents view. (It used to mean loose-only, which read
    as data loss: a chat started inside a project silently vanished from the
    default sidebar until you re-scoped to that project.)

    `archived` False (default) → live chats only; True → archived only;
    None → both. `exclude_teammates` leaves out each teammate's own chat: it
    is reached from the teammate, not from Recents.
    """
    sql = "SELECT * FROM chats"
    where: list[str] = []
    params: list[Any] = []
    if exclude_teammates:
        where.append("id NOT IN (SELECT chat_id FROM teammates WHERE chat_id IS NOT NULL)")
    if project_id:
        where.append("project_id = ?")
        params.append(project_id)
    if archived is True:
        where.append("archived_at IS NOT NULL")
    elif archived is False:
        where.append("archived_at IS NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_chat(r) for r in rows]


def set_chat_archived(chat_id: str, archived: bool) -> bool:
    """Archive (or restore) a chat. Archived chats leave Recents and the
    project list but keep everything: transcript, runs, project membership."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE chats SET archived_at = ? WHERE id = ?",
            (utcnow_iso() if archived else None, chat_id),
        )
    return cur.rowcount > 0


def get_chat(chat_id: str) -> ChatRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return _row_to_chat(row) if row else None


def delete_runs(run_ids: list[str]) -> int:
    """Delete run rows (and the subagent children recorded under them), with
    their artifact and read/star rows. Files the runs wrote stay on disk.
    Returns the number of top-level rows removed."""
    ids = [r for r in run_ids if r]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    with _lock, _conn() as conn:
        children = [
            row["id"] for row in conn.execute(
                f"SELECT id FROM runs WHERE parent_run_id IN ({marks}) AND source = 'subagent'", ids
            ).fetchall()
        ]
        everything = ids + children
        emarks = ",".join("?" for _ in everything)
        with _tx(conn):
            conn.execute(f"DELETE FROM artifacts WHERE run_id IN ({emarks})", everything)
            conn.execute(f"DELETE FROM output_state WHERE run_id IN ({emarks})", everything)
            conn.execute(f"DELETE FROM run_events WHERE run_id IN ({emarks})", everything)
            cur = conn.execute(f"DELETE FROM runs WHERE id IN ({marks})", ids)
            if children:
                cmarks = ",".join("?" for _ in children)
                conn.execute(f"DELETE FROM runs WHERE id IN ({cmarks})", children)
        return cur.rowcount


def delete_chat(chat_id: str) -> bool:
    """Delete a chat and the conversation that only it could reach: its own
    turns (chat + synthesis runs, with their stored transcripts) and its
    history marker. Background runs the chat spawned are outputs in their own
    right and stay; a turn that is still running is left to finish."""
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        if cur.rowcount == 0:
            return False
        turns = [
            row["id"] for row in conn.execute(
                "SELECT id FROM runs WHERE session_id = ? AND source IN ('chat', 'synthesis') "
                "AND status NOT IN ('running', 'pending')",
                (chat_id,),
            ).fetchall()
        ]
        conn.execute("DELETE FROM app_settings WHERE key = ?", (f"chat_history_db:{chat_id}",))
        conn.execute("DELETE FROM answer_feedback WHERE session_id = ?", (chat_id,))
        # A teammate whose chat is deleted starts a new one on its next message.
        conn.execute("UPDATE teammates SET chat_id = NULL WHERE chat_id = ?", (chat_id,))
    for i in range(0, len(turns), 500):
        delete_runs(turns[i:i + 500])
    return True


def rename_chat(chat_id: str, title: str) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute("UPDATE chats SET title = ? WHERE id = ?", (title, chat_id))
        return cur.rowcount > 0


def move_chat_to_project(chat_id: str, project_id: str | None) -> bool:
    """Reassign a chat to a project, or to none (a loose chat) with None."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE chats SET project_id = ?, updated_at = ? WHERE id = ?",
            (project_id, utcnow_iso(), chat_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Projects (Claude-Cowork-style containers for related chats + memory)
# ---------------------------------------------------------------------------

# Teammates
# ---------------------------------------------------------------------------

@dataclass
class TeammateRecord:
    id: str
    name: str
    agent: str                    # the role: which agent does the work
    brief: str = ""               # the standing job, in the user's words
    chat_id: str | None = None    # its one chat (SDK session); None until first used
    project_id: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    @staticmethod
    def new(name: str, agent: str, brief: str = "", project_id: str | None = None) -> TeammateRecord:
        return TeammateRecord(
            id=str(uuid.uuid4()), name=name, agent=agent, brief=brief, project_id=project_id,
        )


def _row_to_teammate(r: sqlite3.Row) -> TeammateRecord:
    return TeammateRecord(
        id=r["id"], name=r["name"], agent=r["agent"], brief=r["brief"] or "",
        chat_id=r["chat_id"], project_id=r["project_id"],
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


def insert_teammate(record: TeammateRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO teammates
               (id, name, agent, brief, chat_id, project_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.id, record.name, record.agent, record.brief, record.chat_id,
             record.project_id, record.created_at, record.updated_at),
        )


def list_teammates() -> list[TeammateRecord]:
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT * FROM teammates ORDER BY created_at").fetchall()
    return [_row_to_teammate(r) for r in rows]


def get_teammate(teammate_id: str) -> TeammateRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM teammates WHERE id = ?", (teammate_id,)).fetchone()
    return _row_to_teammate(row) if row else None


def get_teammate_by_chat(chat_id: str) -> TeammateRecord | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM teammates WHERE chat_id = ?", (chat_id,)).fetchone()
    return _row_to_teammate(row) if row else None


def update_teammate(teammate_id: str, *, name: str, agent: str, brief: str) -> TeammateRecord | None:
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE teammates SET name = ?, agent = ?, brief = ?, updated_at = ? WHERE id = ?",
            (name, agent, brief, utcnow_iso(), teammate_id),
        )
        if cur.rowcount == 0:
            return None
    return get_teammate(teammate_id)


def set_teammate_chat(teammate_id: str, chat_id: str | None) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE teammates SET chat_id = ?, updated_at = ? WHERE id = ?",
            (chat_id, utcnow_iso(), teammate_id),
        )


def delete_teammate(teammate_id: str) -> bool:
    """Remove a teammate. Its automations are kept as plain automations and
    its chat as an ordinary chat: nothing it produced is deleted."""
    with _lock, _conn() as conn:
        cur = conn.execute("DELETE FROM teammates WHERE id = ?", (teammate_id,))
        if cur.rowcount == 0:
            return False
        conn.execute("UPDATE schedules SET teammate_id = NULL WHERE teammate_id = ?", (teammate_id,))
    return True


def set_schedule_teammate(name: str, teammate_id: str | None) -> ScheduleRecord | None:
    """Give an automation to a teammate, or take it back (None)."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE schedules SET teammate_id = ? WHERE name = ?", (teammate_id, name)
        )
        if cur.rowcount == 0:
            return None
    return get_schedule(name)


@dataclass
class ProjectRecord:
    id: str
    name: str
    description: str = ""
    instructions: str = ""    # the inline memory blob — appended to chats' system prompt
    work_dir: str = ""        # optional real folder agents may read AND write ("" = none)
    # The auto-written brief (see bran.project_summary): what the project is
    # about and where it stands. Shown on the project page and prepended to
    # every project chat. summary_at = when it was last written.
    summary: str = ""
    summary_at: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    @staticmethod
    def new(name: str, description: str = "", instructions: str = "") -> ProjectRecord:
        return ProjectRecord(
            id=str(uuid.uuid4()),
            name=name, description=description, instructions=instructions,
        )


def _row_to_project(r: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        id=r["id"], name=r["name"],
        description=r["description"] or "", instructions=r["instructions"] or "",
        work_dir=r["work_dir"] or "",
        summary=r["summary"] or "",
        summary_at=r["summary_at"],
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


def insert_project(record: ProjectRecord) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            """INSERT INTO projects (id, name, description, instructions, work_dir, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record.id, record.name, record.description, record.instructions,
             record.work_dir, record.created_at, record.updated_at),
        )


def update_project(record: ProjectRecord) -> None:
    record.updated_at = utcnow_iso()
    with _lock, _conn() as conn:
        conn.execute(
            """UPDATE projects SET
                 name = ?, description = ?, instructions = ?, work_dir = ?, updated_at = ?
               WHERE id = ?""",
            (record.name, record.description, record.instructions, record.work_dir,
             record.updated_at, record.id),
        )


def set_project_summary(project_id: str, text: str) -> bool:
    with _lock, _conn() as conn:
        cur = conn.execute(
            "UPDATE projects SET summary = ?, summary_at = ? WHERE id = ?",
            (text, utcnow_iso(), project_id),
        )
    return cur.rowcount > 0


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
    """Remove a project + its memory entries, in one transaction. Nothing else
    is deleted: its chats become loose, and its runners and runs are detached
    (project_id NULL) so nothing keeps pointing at a project that is gone.
    Runners that only make sense WITH the project (its daily summary) are the
    caller's to remove first — see bran.schedule_service.delete_project."""
    with _lock, _conn() as conn, _tx(conn):
        for table in ("chats", "schedules", "runs"):
            conn.execute(
                f"UPDATE {table} SET project_id = NULL WHERE project_id = ?", (project_id,)
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
    def new(project_id: str, text: str) -> ProjectMemory:
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
            "SELECT project_id, COUNT(*) AS n FROM chats WHERE archived_at IS NULL GROUP BY project_id"
        ).fetchall()
    return {r["project_id"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# App settings — single-user key-value pairs (e.g. the global "About me")
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ---------------------------------------------------------------------------
# Answer feedback (thumbs up/down in the chat)
# ---------------------------------------------------------------------------

def set_answer_feedback(
    session_id: str, answer_key: str, rating: int, *,
    prompt: str = "", answer: str = "", agent: str = "",
) -> None:
    """Record a rating (1 / -1) for one answer; 0 clears it."""
    with _lock, _conn() as conn:
        if rating == 0:
            conn.execute(
                "DELETE FROM answer_feedback WHERE session_id = ? AND answer_key = ?",
                (session_id, answer_key),
            )
            return
        conn.execute(
            "INSERT INTO answer_feedback "
            "(session_id, answer_key, rating, prompt, answer, agent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id, answer_key) DO UPDATE SET "
            "rating = excluded.rating, prompt = excluded.prompt, answer = excluded.answer, "
            "agent = excluded.agent, created_at = excluded.created_at",
            (session_id, answer_key, 1 if rating > 0 else -1, prompt, answer, agent, utcnow_iso()),
        )


def list_answer_feedback(session_id: str) -> dict[str, int]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT answer_key, rating FROM answer_feedback WHERE session_id = ?", (session_id,)
        ).fetchall()
    return {r["answer_key"]: r["rating"] for r in rows}


def all_answer_feedback() -> list[dict[str, Any]]:
    """Every rated answer, newest first (`bran feedback`)."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT session_id, rating, prompt, answer, agent, created_at "
            "FROM answer_feedback ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Retention — what would otherwise grow without bound.
# ---------------------------------------------------------------------------

def prune_run_events(older_than_days: int) -> int:
    """Drop the stored transcripts of runs that ended more than N days ago.
    The run row, its result and its artifacts stay; a pruned conversation
    replays as prompt + reply. Returns how many transcripts were dropped."""
    if older_than_days <= 0:
        return 0
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    with _lock, _conn() as conn:
        cur = conn.execute(
            "DELETE FROM run_events WHERE run_id IN ("
            "  SELECT id FROM runs WHERE COALESCE(ended_at, started_at) < ?"
            "   AND status NOT IN ('running', 'pending'))"
            " OR run_id NOT IN (SELECT id FROM runs)",
            (cutoff,),
        )
        return cur.rowcount


def vacuum() -> None:
    """Rebuild the file so freed pages go back to the filesystem. Needs free
    disk about the size of the database and blocks writers while it runs —
    a CLI job (`bran prune --vacuum`), not something the server does."""
    with _lock, _conn() as conn:
        conn.execute("VACUUM")


# ---------------------------------------------------------------------------
# Schema versioning. A fresh file gets the current shape and the latest
# version in one go; an existing file runs only the migrations it has not seen
# (PRAGMA user_version counts them), each exactly once.
# ---------------------------------------------------------------------------

def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not any(c["name"] == column for c in cols):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _m1_legacy_catch_up(conn: sqlite3.Connection) -> None:
    """Everything the old run-on-every-start migrations did, once: columns
    added over time, their backfills, the Inbox pseudo-project's removal and
    the artifacts backfill. Every statement is idempotent, so a database of
    any age lands on the same shape."""
    for table, column, decl in (
        ("chats", "project_id", "TEXT"),
        ("chats", "archived_at", "TEXT"),
        ("runs", "project_id", "TEXT"),
        ("runs", "source", "TEXT"),
        ("runs", "schedule_id", "TEXT"),
        ("runs", "actor", "TEXT"),
        ("schedules", "project_id", "TEXT"),
        ("schedules", "run_at", "TEXT"),
        ("schedules", "verify", "INTEGER NOT NULL DEFAULT 0"),
        ("schedules", "delta", "INTEGER NOT NULL DEFAULT 0"),
        ("schedules", "alert", "TEXT NOT NULL DEFAULT ''"),
        ("projects", "work_dir", "TEXT NOT NULL DEFAULT ''"),
        ("projects", "summary", "TEXT NOT NULL DEFAULT ''"),
        ("projects", "summary_at", "TEXT"),
    ):
        _add_column_if_missing(conn, table, column, decl)
    # `source`: a run whose session is a chat is a chat turn; everything else
    # can only be called "manual" in hindsight.
    conn.execute(
        "UPDATE runs SET source = 'chat' "
        "WHERE source IS NULL AND session_id IN (SELECT id FROM chats)"
    )
    conn.execute("UPDATE runs SET source = 'manual' WHERE source IS NULL")
    # The Inbox pseudo-project is gone: NULL project_id simply means loose.
    for table in ("chats", "runs", "schedules"):
        conn.execute(f"UPDATE {table} SET project_id = NULL WHERE project_id = 'inbox'")
    conn.execute("DELETE FROM projects WHERE id = 'inbox'")
    # Artifacts used to be a list inside runs.metadata.
    if not conn.execute("SELECT 1 FROM app_settings WHERE key = 'artifacts_migrated'").fetchone():
        now = utcnow_iso()
        for r in conn.execute(
            "SELECT id, metadata FROM runs WHERE metadata LIKE '%\"artifacts\"%'"
        ).fetchall():
            try:
                paths = json.loads(r["metadata"] or "{}").get("artifacts") or []
            except (ValueError, TypeError):
                continue
            for p in paths:
                if isinstance(p, str) and p:
                    conn.execute(
                        "INSERT OR IGNORE INTO artifacts (run_id, path, created_at) VALUES (?, ?, ?)",
                        (r["id"], p, now),
                    )
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('artifacts_migrated', '1')"
        )


def _m2_events_to_table(conn: sqlite3.Connection) -> None:
    """Move each run's stored transcript out of runs.metadata into run_events
    (see the table's comment). The file does not shrink until a VACUUM
    (`bran prune --vacuum`), but the freed pages are reused."""
    has_events = (
        "metadata LIKE '%\"events\"%' AND "
        "CASE WHEN json_valid(metadata) THEN json_type(metadata, '$.events') END"
    )
    conn.execute(
        "INSERT OR REPLACE INTO run_events (run_id, events) "
        f"SELECT id, json_extract(metadata, '$.events') FROM runs WHERE {has_events} = 'array'"
    )
    conn.execute(
        f"UPDATE runs SET metadata = json_remove(metadata, '$.events') WHERE {has_events} IS NOT NULL"
    )


def _m3_answer_feedback(conn: sqlite3.Connection) -> None:
    """The answer_feedback table. Nothing to reshape: `_TABLES` (run before
    the migrations) creates it; this step only marks the version at which an
    existing database gained it."""


def _m4_teammates(conn: sqlite3.Connection) -> None:
    """Teammates: `_TABLES` creates the table; an existing schedules table
    gains the column that ties an automation to one."""
    _add_column_if_missing(conn, "schedules", "teammate_id", "TEXT")


# Append only. A migration never changes once released: a database that has
# already run it will not run it again.
_MIGRATIONS = (_m1_legacy_catch_up, _m2_events_to_table, _m3_answer_feedback, _m4_teammates)


def _bring_up_to_date(conn: sqlite3.Connection) -> None:
    latest = len(_MIGRATIONS)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    fresh = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
    ).fetchone() is None
    if version >= latest and not fresh:
        return
    # Tables first (IF NOT EXISTS: a legacy file keeps its own, older-shaped
    # ones and merely gains the tables it lacks), then the steps that reshape
    # legacy tables, then the indexes — which need the reshaped columns.
    conn.executescript(_TABLES)
    if not fresh:
        for number, migrate in enumerate(_MIGRATIONS[version:], start=version + 1):
            with _tx(conn):
                migrate(conn)
                conn.execute(f"PRAGMA user_version = {number}")
    conn.executescript(_INDEXES)
    conn.execute(f"PRAGMA user_version = {latest}")


def init_db() -> None:
    """Make sure the database exists and is current (also done lazily on
    first access)."""
    _ensure_ready()
