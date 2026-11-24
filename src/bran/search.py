"""Search over what bran has already produced: past runs and saved briefings.

Agents could not look anything up in bran's own history: months of briefings
were flat files and past results were only reachable by run id. This indexes
both for full-text search, so a claim can be traced to the run or the file
(and the date) it came from.

Two SQLite FTS5 tables in bran's database, filled lazily:

- `run_docs`: one row per finished run that produced a result (task + result).
  Brought up to date at the start of every search from a high-water mark on
  `runs.ended_at`, so nothing is added to the hot path of a run. A hit whose
  run has since been deleted is dropped and forgotten.
- `briefing_docs`: one row per section of each `*.md` in the briefings folder
  (split at headings, so a hit points at the section, not a whole day). A file
  is re-indexed when its size or modification time changes.

Where SQLite was built without FTS5 the same functions answer by scanning
(LIKE over runs, a read of the files): slower, same results in spirit.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bran.config import SETTINGS

# The store's own connection and lock (same package): the index lives in
# bran's database and is written under the same discipline as everything else.
from bran.persistence import _conn, _lock, _tx, get_setting, set_setting

_RUNS_MARK = "search_runs_indexed_to"
_BRIEFINGS_STATE = "search_briefings_state"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_TERM = re.compile(r'"([^"]+)"|(\S+)')
_fts5: bool | None = None


class SearchError(ValueError):
    """Something the caller can put right; the message is written for an agent."""


def fts5_available() -> bool:
    """Whether this SQLite can do full-text search (checked once)."""
    global _fts5
    if _fts5 is None:
        try:
            with _lock, _conn() as conn:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.bran_fts_probe USING fts5(x)")
                conn.execute("DROP TABLE IF EXISTS temp.bran_fts_probe")
            _fts5 = True
        except sqlite3.Error:
            _fts5 = False
    return _fts5


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS run_docs USING fts5("
        "run_id UNINDEXED, agent UNINDEXED, source UNINDEXED, day UNINDEXED, task, result, "
        "tokenize='porter unicode61')"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS briefing_docs USING fts5("
        "path UNINDEXED, day UNINDEXED, section, text, tokenize='porter unicode61')"
    )


def _terms(query: str) -> list[str]:
    """The words and "quoted phrases" of a search, as the user wrote them."""
    out = [(a or b).strip() for a, b in _TERM.findall(query or "")]
    return [t for t in out if re.search(r"\w", t)][:12]


def _match(terms: list[str], any_term: bool = False) -> str:
    """An FTS5 expression for the terms: each one quoted, so nothing in the
    query is read as search syntax."""
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    return (" OR " if any_term else " ").join(quoted)


def _day(value: str | None, name: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if not _DAY.fullmatch(value):
        raise SearchError(f"`{name}` must be a date like 2026-11-24.")
    return value


def _snippet(text: str, terms: list[str], width: int = 220) -> str:
    """The stretch of `text` around the first term found, for the scan path."""
    flat = " ".join((text or "").split())
    low = flat.lower()
    at = min((i for i in (low.find(t.lower()) for t in terms) if i >= 0), default=0)
    start = max(0, at - width // 3)
    piece = flat[start:start + width]
    return ("… " if start else "") + piece + (" …" if start + width < len(flat) else "")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def sync_runs() -> int:
    """Index every run finished since the last look. Returns how many."""
    if not fts5_available():
        return 0
    mark = get_setting(_RUNS_MARK) or ""
    with _lock, _conn() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT id, agent, source, started_at, ended_at, task, result FROM runs "
            "WHERE ended_at IS NOT NULL AND ended_at >= ? AND source != 'subagent' "
            "AND coalesce(result, '') != '' ORDER BY ended_at",
            (mark,),
        ).fetchall()
        if not rows:
            return 0
        with _tx(conn):
            if not mark:
                conn.execute("DELETE FROM run_docs")
            for r in rows:
                if mark:
                    conn.execute("DELETE FROM run_docs WHERE run_id = ?", (r["id"],))
                conn.execute(
                    "INSERT INTO run_docs (run_id, agent, source, day, task, result) VALUES (?, ?, ?, ?, ?, ?)",
                    (r["id"], r["agent"], r["source"], (r["started_at"] or "")[:10], r["task"] or "", r["result"]),
                )
        newest = rows[-1]["ended_at"]
    set_setting(_RUNS_MARK, newest)
    return len(rows)


def search_runs(
    query: str, *, agent: str | None = None, since: str | None = None, limit: int = 10,
) -> list[dict[str, Any]]:
    """Runs whose task or result match, best first: run id, date, agent,
    source and a snippet."""
    terms = _terms(query)
    if not terms:
        raise SearchError("`query` is required: the words to look for.")
    since = _day(since, "since")
    agent = (agent or "").strip() or None
    limit = max(1, min(50, int(limit)))
    if not fts5_available():
        return _scan_runs(terms, agent, since, limit)
    sync_runs()
    sql = (
        "SELECT d.rowid AS doc, d.run_id, d.agent, d.source, d.day, "
        "snippet(run_docs, -1, '[', ']', ' … ', 28) AS snip, "
        "(SELECT 1 FROM runs r WHERE r.id = d.run_id) AS alive "
        "FROM run_docs d WHERE run_docs MATCH ?"
    )
    filters: list[Any] = []
    if agent:
        sql += " AND d.agent = ?"
        filters.append(agent)
    if since:
        sql += " AND d.day >= ?"
        filters.append(since)
    sql += " ORDER BY bm25(run_docs), d.day DESC LIMIT ?"
    hits: list[dict[str, Any]] = []
    with _lock, _conn() as conn:
        for any_term in (False, True):
            rows = conn.execute(sql, [_match(terms, any_term), *filters, limit * 2]).fetchall()
            gone = [r["doc"] for r in rows if not r["alive"]]
            for doc in gone:  # the run was deleted since it was indexed
                conn.execute("DELETE FROM run_docs WHERE rowid = ?", (doc,))
            hits = [
                {"run_id": r["run_id"], "date": r["day"], "agent": r["agent"], "source": r["source"],
                 "snippet": " ".join((r["snip"] or "").split())}
                for r in rows if r["alive"]
            ][:limit]
            if hits or len(terms) == 1:
                break
    return hits


def _scan_runs(terms: list[str], agent: str | None, since: str | None, limit: int) -> list[dict[str, Any]]:
    sql = "SELECT id, agent, source, started_at, task, result FROM runs WHERE source != 'subagent'"
    params: list[Any] = []
    for t in terms:
        sql += " AND (instr(lower(coalesce(result, '')), lower(?)) > 0 OR instr(lower(coalesce(task, '')), lower(?)) > 0)"
        params += [t, t]
    if agent:
        sql += " AND agent = ?"
        params.append(agent)
    if since:
        sql += " AND started_at >= ?"
        params.append(since)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _lock, _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"run_id": r["id"], "date": (r["started_at"] or "")[:10], "agent": r["agent"], "source": r["source"],
         "snippet": _snippet(f"{r['task'] or ''} {r['result'] or ''}", terms)}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Briefings
# ---------------------------------------------------------------------------

def briefing_files() -> list[Path]:
    folder = SETTINGS.briefings_dir
    return sorted(p for p in folder.glob("*.md") if p.is_file()) if folder.is_dir() else []


def _file_day(path: Path) -> str:
    m = _DAY.search(path.name)
    if m:
        return m.group(0)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()


def sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) for each part of a briefing. What comes before the
    first heading belongs to the document's title."""
    out: list[tuple[str, str]] = []
    title, body = "", []
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            if "".join(body).strip():
                out.append((title or "Opening", "\n".join(body).strip()))
            title, body = m.group(2).strip().strip("*").strip(), []
        else:
            body.append(line)
    if "".join(body).strip():
        out.append((title or "Opening", "\n".join(body).strip()))
    return out


def index_briefings(force: bool = False) -> dict[str, int]:
    """Bring the briefing index up to date: new and changed files are
    (re)indexed, vanished ones forgotten. Safe to run as often as you like;
    `force` rebuilds it from nothing."""
    files = briefing_files()
    if not fts5_available():
        return {"files": len(files), "indexed": 0, "removed": 0, "sections": 0}
    try:
        state: dict[str, list[int]] = {} if force else json.loads(get_setting(_BRIEFINGS_STATE) or "{}")
    except ValueError:
        state = {}
    indexed = removed = added = 0
    with _lock, _conn() as conn:
        _ensure_tables(conn)
        with _tx(conn):
            if force:
                conn.execute("DELETE FROM briefing_docs")
            seen: dict[str, list[int]] = {}
            for path in files:
                stat = path.stat()
                key, stamp = str(path), [stat.st_mtime_ns, stat.st_size]
                seen[key] = stamp
                if state.get(key) == stamp:
                    continue
                conn.execute("DELETE FROM briefing_docs WHERE path = ?", (key,))
                day = _file_day(path)
                for heading, body in sections(path.read_text(encoding="utf-8", errors="replace")):
                    conn.execute(
                        "INSERT INTO briefing_docs (path, day, section, text) VALUES (?, ?, ?, ?)",
                        (key, day, heading, body),
                    )
                    added += 1
                indexed += 1
            for key in set(state) - set(seen):
                conn.execute("DELETE FROM briefing_docs WHERE path = ?", (key,))
                removed += 1
    set_setting(_BRIEFINGS_STATE, json.dumps(seen))
    return {"files": len(files), "indexed": indexed, "removed": removed, "sections": added}


def search_briefings(
    query: str, *, since: str | None = None, until: str | None = None, limit: int = 10,
) -> list[dict[str, Any]]:
    """Briefing sections that match, best first: the file, its date, the
    section heading and a snippet."""
    terms = _terms(query)
    if not terms:
        raise SearchError("`query` is required: the words to look for.")
    since, until = _day(since, "since"), _day(until, "until")
    limit = max(1, min(50, int(limit)))
    if not fts5_available():
        return _scan_briefings(terms, since, until, limit)
    index_briefings()
    sql = (
        "SELECT path, day, section, snippet(briefing_docs, 3, '[', ']', ' … ', 32) AS snip "
        "FROM briefing_docs WHERE briefing_docs MATCH ?"
    )
    filters: list[Any] = []
    if since:
        sql += " AND day >= ?"
        filters.append(since)
    if until:
        sql += " AND day <= ?"
        filters.append(until)
    sql += " ORDER BY bm25(briefing_docs), day DESC LIMIT ?"
    rows: list[sqlite3.Row] = []
    with _lock, _conn() as conn:
        for any_term in (False, True):
            rows = conn.execute(sql, [_match(terms, any_term), *filters, limit]).fetchall()
            if rows or len(terms) == 1:
                break
    return [
        {"path": r["path"], "date": r["day"], "section": r["section"],
         "snippet": " ".join((r["snip"] or "").split())}
        for r in rows
    ]


def _scan_briefings(terms: list[str], since: str | None, until: str | None, limit: int) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in reversed(briefing_files()):
        day = _file_day(path)
        if (since and day < since) or (until and day > until):
            continue
        for heading, body in sections(path.read_text(encoding="utf-8", errors="replace")):
            low = f"{heading} {body}".lower()
            if all(t.lower() in low for t in terms):
                hits.append({"path": str(path), "date": day, "section": heading, "snippet": _snippet(body, terms)})
                if len(hits) >= limit:
                    return hits
    return hits
