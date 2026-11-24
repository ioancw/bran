"""Datasets: tables agents hand to each other by handle, never by re-typing.

An agent writes a table ONCE (`write`), gets back a short handle (`ds_a1f3c9`)
and a small manifest (columns with types, row count, the first rows, where the
numbers came from, what to be careful about), and from then on passes the
handle. Any join, comparison or arithmetic is done by SQL (`query`) in a
deterministic engine, and its result is a new dataset that records the SQL and
the datasets it came from. A model never transcribes rows, so they cannot be
dropped or drift.

Storage is bran's own SQLite database: one row of metadata per dataset
(`datasets`) and one JSON object per row (`dataset_rows`). DuckDB is only the
query engine, and it never opens that database: each query gets a fresh
in-memory DuckDB holding typed copies of just the datasets its SQL names. The
chats, runs and settings in bran's database are therefore out of a query's
reach by construction, and nothing a query does can change a stored dataset.

The engine is locked down twice over (see `_locked_connection` and
`_check_statement`): by configuration (no file system, no network, no
extensions, limits on memory and time) and by parsing (one SELECT, nothing
else). Neither relies on what a prompt says.

Storage choice (the plan's open question, settled by a spike): JSON per row,
not a typed SQLite table per dataset. Typed tables would only have helped if
DuckDB read them straight from SQLite, which needs its sqlite extension and
would have put the whole of bran's database in front of agent-written SQL.
Loading JSON rows into typed DuckDB tables took 0.3 s for 100,000 rows.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import threading
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from bran.persistence import (
    DatasetRecord,
    dataset_rows_json,
    get_dataset,
    insert_dataset,
)

MAX_ROWS = 100_000
MAX_COLUMNS = 200
MAX_CELL_CHARS = 10_000
MAX_CSV_BYTES = 20 * 1024 * 1024
QUERY_TIMEOUT_S = 20.0
QUERY_MEMORY = "512MB"
PREVIEW_ROWS = 5

# The column types a manifest can state. Everything a query returns is mapped
# onto these (see `_normalise`), so a manifest's types are always accurate.
TYPES = ("BIGINT", "DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP", "VARCHAR")

HANDLE = re.compile(r"\bds_[0-9a-f]{6}\b")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}(:?\d{2})?)?")
_INT = re.compile(r"[+-]?\d{1,18}")
_FLOAT = re.compile(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")
# Catalogue and settings functions tell a query about the machine (paths, the
# configuration). A query has no use for them: the manifest states the schema.
_INTROSPECTION = re.compile(
    r"\b(duckdb_\w*|pragma_\w*|current_setting|getvariable)\s*\(|"
    r"\b(information_schema|pg_catalog|sqlite_master|sqlite_schema|duckdb_\w+)\b",
    re.IGNORECASE,
)


class DatasetError(ValueError):
    """Something an agent can put right. The message is written for it."""


# ---------------------------------------------------------------------------
# Shaping rows: column names, types, values
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _reserved_words() -> frozenset[str]:
    """The words SQL will not take as a plain column name, from the engine
    itself: a column called `order` (or `at`, or `left`) would have to be
    quoted in every query, so it is renamed on the way in."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            "SELECT keyword_name FROM duckdb_keywords() "
            "WHERE keyword_category IN ('reserved', 'type_function')"
        ).fetchall()
    finally:
        con.close()
    return frozenset(r[0].lower() for r in rows)


def column_names(keys: list[str]) -> list[str]:
    """Names a query can use without quoting: lower-case letters, digits and
    underscores, not a reserved word, each one distinct."""
    out: list[str] = []
    for key in keys:
        name = re.sub(r"[^0-9a-zA-Z_]+", "_", str(key).strip()).strip("_").lower() or "column"
        if name[0].isdigit():
            name = "c_" + name
        if name in _reserved_words():
            name += "_"
        base, n = name, 2
        while name in out:
            name = f"{base}_{n}"
            n += 1
        out.append(name)
    return out


def _kind(value: Any) -> str | None:
    """The type one stored value has; None for a missing value."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT" if -(2**63) <= value < 2**63 else "DOUBLE"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, str):
        if _DATE.fullmatch(value) and _is_date(value):
            return "DATE"
        if _TIMESTAMP.fullmatch(value) and _is_timestamp(value):
            return "TIMESTAMP"
    return "VARCHAR"


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _is_timestamp(text: str) -> bool:
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def infer_type(values: list[Any], hint: str = "VARCHAR") -> str:
    """The one type that holds every value of a column. Whole numbers and
    decimals together are DOUBLE; dates and times together are TIMESTAMP; any
    other mixture is text. A column with no values takes `hint`."""
    kinds = {k for k in map(_kind, values) if k is not None}
    if not kinds:
        return hint if hint in TYPES else "VARCHAR"
    if len(kinds) == 1:
        return next(iter(kinds))
    if kinds <= {"BIGINT", "DOUBLE"}:
        return "DOUBLE"
    if kinds <= {"DATE", "TIMESTAMP"}:
        return "TIMESTAMP"
    return "VARCHAR"


def _plain(value: Any) -> Any:
    """A value as JSON can carry it. Used for what a query returns and for
    what an agent hands in: dates become ISO text, decimals become numbers,
    nested values become their JSON text, and not-a-number becomes missing."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() and abs(value) < 2**63 else float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep="T")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return str(value)


def _as_text(value: Any) -> str | None:
    """How a value reads in a text column."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if isinstance(value, str) else str(value)


def shape(keys: list[str], rows: list[list[Any]], hints: list[str] | None = None) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """(columns, stored rows) for a table given as column keys and rows of
    values in that order. Raises DatasetError when it is too big to be a
    table."""
    if not keys:
        raise DatasetError("A dataset needs at least one column.")
    if len(keys) > MAX_COLUMNS:
        raise DatasetError(f"{len(keys)} columns is more than the {MAX_COLUMNS} a dataset may have.")
    if len(rows) > MAX_ROWS:
        raise DatasetError(
            f"{len(rows):,} rows is more than the {MAX_ROWS:,} a dataset may hold. "
            "Aggregate or filter it first."
        )
    names = column_names(keys)
    cells = [[_plain(v) for v in row] for row in rows]
    for row in cells:
        for v in row:
            if isinstance(v, str) and len(v) > MAX_CELL_CHARS:
                raise DatasetError(
                    f"A cell holds {len(v):,} characters; the limit is {MAX_CELL_CHARS:,}. "
                    "Datasets are for tables, not documents."
                )
    columns: list[dict[str, str]] = []
    for i, name in enumerate(names):
        hint = hints[i] if hints and i < len(hints) else "VARCHAR"
        kind = infer_type([row[i] for row in cells], hint)
        columns.append({"name": name, "type": kind})
        if kind == "VARCHAR":
            for row in cells:
                row[i] = _as_text(row[i])
        elif kind == "DOUBLE":
            for row in cells:
                if isinstance(row[i], int) and not isinstance(row[i], bool):
                    row[i] = float(row[i])
    return columns, [dict(zip(names, row, strict=True)) for row in cells]


def _from_objects(rows: Any) -> tuple[list[str], list[list[Any]]]:
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except ValueError as e:
            raise DatasetError(f"`rows` is not valid JSON: {e}") from e
    if not isinstance(rows, list) or not rows:
        raise DatasetError("`rows` must be a non-empty JSON array of objects, one per row.")
    keys: list[str] = []
    for i, row in enumerate(rows[: MAX_ROWS + 1]):
        if not isinstance(row, dict):
            raise DatasetError(f"`rows[{i}]` is not an object. Each row is an object: column name to value.")
        for k in row:
            if k not in keys:
                keys.append(k)
    return keys, [[row.get(k) for k in keys] for row in rows]


def _coerce(text: str) -> Any:
    """A CSV cell as the value it writes down."""
    s = text.strip()
    if s == "":
        return None
    if _INT.fullmatch(s):
        return int(s)
    if _FLOAT.fullmatch(s):
        return float(s)
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    return s


def _from_csv(csv_path: str) -> tuple[list[str], list[list[Any]]]:
    """Read a CSV the run itself could have written: inside bran's home or the
    project's working folder, nowhere else."""
    from bran.permissions import _default_roots, _is_within, _resolve
    from bran.project_files import files_dir

    target = _resolve(csv_path)
    roots = list(_default_roots())
    from bran.background import current_project_id

    if current_project_id.get():
        roots.append(files_dir(current_project_id.get()).resolve())
    if target is None or not _is_within(target, roots):
        raise DatasetError(
            "`csv_path` must be a file inside bran's home or this project's working folder."
        )
    if not target.is_file():
        raise DatasetError(f"There is no file at {target}.")
    if target.stat().st_size > MAX_CSV_BYTES:
        raise DatasetError(f"The file is larger than the {MAX_CSV_BYTES // (1024 * 1024)} MB a dataset may be read from.")
    text = target.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise DatasetError("The CSV is empty.") from None
    width = len(header)
    rows: list[list[Any]] = []
    for line in reader:
        if not any(cell.strip() for cell in line):
            continue
        if len(rows) >= MAX_ROWS:
            raise DatasetError(f"The CSV has more than the {MAX_ROWS:,} rows a dataset may hold.")
        rows.append([_coerce(c) for c in (line + [""] * width)[:width]])
    if not rows:
        raise DatasetError("The CSV has a header and no rows.")
    return header, rows


# ---------------------------------------------------------------------------
# Writing and reading
# ---------------------------------------------------------------------------

def _new_handle() -> str:
    while True:
        handle = "ds_" + uuid.uuid4().hex[:6]
        if get_dataset(handle) is None:
            return handle


def _clip(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def write(
    name: str, *, rows: Any = None, csv_path: str = "", source: str = "",
    description: str = "", caveats: str = "",
) -> DatasetRecord:
    """Store a table and return its record. The producing run and project come
    from the run that is executing, never from the caller."""
    from bran.background import current_project_id, current_run_id

    name = _clip(name, 120)
    source = _clip(source, 500)
    if not name:
        raise DatasetError("`name` is required: a few words saying what the table is.")
    if not source:
        raise DatasetError(
            "`source` is required: where the numbers came from (a URL, a file, a briefing) and their as-of date."
        )
    given_rows = rows is not None and rows != "" and rows != []
    if given_rows == bool(csv_path):
        raise DatasetError("Give exactly one of `rows` (a JSON array of objects) or `csv_path`.")
    keys, values = _from_objects(rows) if given_rows else _from_csv(csv_path)
    columns, stored = shape(keys, values)
    record = DatasetRecord(
        id=_new_handle(), name=name, columns=columns, row_count=len(stored),
        description=_clip(description, 500), run_id=current_run_id.get(),
        project_id=current_project_id.get(), source=source, caveats=_clip(caveats, 1000),
    )
    insert_dataset(record, stored)
    return record


def require(handle: str) -> DatasetRecord:
    handle = (handle or "").strip()
    record = get_dataset(handle) if HANDLE.fullmatch(handle) else None
    if record is None:
        raise DatasetError(
            f"There is no dataset {handle!r}. A handle looks like ds_a1f3c9; list_datasets shows the ones that exist."
        )
    return record


def rows_of(record: DatasetRecord, limit: int | None = None) -> list[dict[str, Any]]:
    return [json.loads(text) for text in dataset_rows_json(record.id, limit)]


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------

def _check_statement(sql: str) -> None:
    """One SELECT and nothing else. Decided by the engine's own parser, so a
    comment or a second statement cannot smuggle anything past."""
    import duckdb

    if _INTROSPECTION.search(sql):
        raise DatasetError(
            "Catalogue and settings functions are not available. The manifest states each dataset's columns."
        )
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as e:
        raise DatasetError(f"The SQL did not parse: {_engine_message(e)}") from e
    if len(statements) != 1:
        raise DatasetError("Give exactly one SQL statement.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise DatasetError(
            "Only a single SELECT (or WITH ... SELECT) is allowed: a query reads datasets and returns "
            "a new one. It cannot create, change, copy, attach or configure anything."
        )


def _engine_message(error: Exception) -> str:
    """The engine's complaint, short enough to act on."""
    text = str(error).strip()
    return text[:600] + ("…" if len(text) > 600 else "")


def _locked_connection():
    """A throwaway in-memory engine that cannot see the machine: no files, no
    network, no extensions; bounded memory. The lock is applied by the caller
    once the datasets are loaded, after which no setting can be changed."""
    import duckdb

    return duckdb.connect(":memory:", config={
        "enable_external_access": "false",
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "memory_limit": QUERY_MEMORY,
        "threads": "2",
    })


def _load(con: Any, record: DatasetRecord) -> None:
    """Put a typed copy of a stored dataset into the engine under its handle."""
    # Names are sanitised on the way in and types come from a fixed set, so
    # this structure is safe to write into the statement.
    structure = json.dumps([{c["name"]: c["type"] for c in record.columns}]).replace("'", "''")
    blob = "[" + ",".join(dataset_rows_json(record.id)) + "]"
    con.execute(
        f'CREATE TABLE "{record.id}" AS SELECT u.* FROM '
        f"(SELECT unnest(from_json(?::JSON, '{structure}')) AS u)",
        [blob],
    )


def _hint(engine_type: str) -> str:
    """Our type for a column the engine typed but returned no values for."""
    t = engine_type.upper()
    if t == "BOOLEAN":
        return "BOOLEAN"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if t == "DATE":
        return "DATE"
    if t in ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "UTINYINT", "USMALLINT", "UINTEGER"):
        return "BIGINT"
    if t in ("DOUBLE", "FLOAT", "REAL", "HUGEINT", "UHUGEINT", "UBIGINT") or t.startswith("DECIMAL"):
        return "DOUBLE"
    return "VARCHAR"


def _run(sql: str, parents: list[DatasetRecord], timeout_s: float) -> tuple[list[str], list[str], list[list[Any]]]:
    """(column names, engine types, rows) of `sql` over `parents`."""
    import duckdb

    con = _locked_connection()
    try:
        for record in parents:
            _load(con, record)
        con.execute("SET lock_configuration = true")
        timer = threading.Timer(timeout_s, con.interrupt)
        timer.daemon = True
        timer.start()
        try:
            cursor = con.execute(sql)
            described = cursor.description or []
            rows = cursor.fetchmany(MAX_ROWS + 1)
        except duckdb.InterruptException:
            raise DatasetError(
                f"The query ran for more than {timeout_s:g} seconds and was stopped. "
                "Filter or aggregate earlier, and check any join has a condition."
            ) from None
        except duckdb.Error as e:
            raise DatasetError(f"SQL error: {_engine_message(e)}") from e
        finally:
            timer.cancel()
    finally:
        con.close()
    return [d[0] for d in described], [str(d[1]) for d in described], [list(r) for r in rows]


def query(sql: str, name: str = "", *, timeout_s: float = QUERY_TIMEOUT_S) -> DatasetRecord:
    """Run one SELECT over stored datasets (named in the SQL by handle) and
    store what it returns as a new dataset that records the SQL and the
    datasets it came from."""
    from bran.background import current_project_id, current_run_id

    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise DatasetError("`sql` is required: one SELECT naming datasets by handle, e.g. SELECT * FROM ds_a1f3c9.")
    _check_statement(sql)
    handles = sorted(set(HANDLE.findall(sql)))
    parents = [require(h) for h in handles]
    keys, engine_types, rows = _run(sql, parents, timeout_s)
    if len(rows) > MAX_ROWS:
        raise DatasetError(
            f"The result has more than {MAX_ROWS:,} rows. Aggregate it, filter it or add a LIMIT."
        )
    columns, stored = shape(keys, rows, hints=[_hint(t) for t in engine_types])
    inherited = "; ".join(dict.fromkeys(p.caveats for p in parents if p.caveats))
    record = DatasetRecord(
        id=_new_handle(), name=_clip(name, 120) or "query result", columns=columns,
        row_count=len(stored), run_id=current_run_id.get(), project_id=current_project_id.get(),
        source="query", caveats=_clip(inherited, 1000), parent_ids=handles, sql=sql,
    )
    insert_dataset(record, stored)
    return record


# ---------------------------------------------------------------------------
# What an agent is shown
# ---------------------------------------------------------------------------

def _cell(value: Any, limit: int = 60) -> str:
    if value is None:
        return ""
    text = _as_text(value) or ""
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def markdown_table(record: DatasetRecord, rows: list[dict[str, Any]], cell_limit: int = 60) -> str:
    names = [c["name"] for c in record.columns]
    lines = ["| " + " | ".join(names) + " |", "|" + "|".join(" --- " for _ in names) + "|"]
    lines += ["| " + " | ".join(_cell(row.get(n), cell_limit) for n in names) + " |" for row in rows]
    return "\n".join(lines)


def manifest(record: DatasetRecord, preview: int = PREVIEW_ROWS) -> str:
    """The small text an agent passes on in place of the table: enough to
    decide what to do with it, never the rows themselves."""
    n = max(0, min(preview, 50))
    shown = rows_of(record, n) if n else []
    cols = ", ".join(f"{c['name']} {c['type']}" for c in record.columns)
    lines = [
        f"Dataset {record.id}: {record.name}",
        f"Rows: {record.row_count:,}. Columns ({len(record.columns)}): {cols}",
        f"Source: {record.source}",
    ]
    if record.description:
        lines.append(f"Description: {record.description}")
    if record.caveats:
        lines.append(f"Caveats: {record.caveats}")
    made = f"Produced {record.created_at[:16].replace('T', ' ')} UTC"
    if record.run_id:
        made += f" by run {record.run_id[:8]}"
    lines.append(made)
    if record.parent_ids:
        lines.append("Derived from: " + ", ".join(record.parent_ids))
    if record.sql:
        lines.append("SQL: " + (record.sql if len(record.sql) <= 1500 else record.sql[:1500] + " …"))
    if shown:
        label = "All rows" if record.row_count <= len(shown) else f"First {len(shown)} rows"
        lines += ["", f"{label}:", markdown_table(record, shown)]
    elif record.row_count == 0:
        lines.append("(no rows)")
    return "\n".join(lines)


def export_csv(record: DatasetRecord) -> Path:
    """Write the whole dataset as a CSV where the user finds their files: the
    project's Files when the run is in a project, else bran's documents."""
    from bran.background import current_project_id
    from bran.doc_export import documents_dir, slugify
    from bran.project_files import files_dir

    project_id = current_project_id.get()
    folder = files_dir(project_id, create=True) if project_id else documents_dir()
    path = folder / f"{slugify(record.name, 'dataset')}-{record.id}.csv"
    names = [c["name"] for c in record.columns]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(names)
        for row in rows_of(record):
            writer.writerow(["" if row.get(n) is None else _as_text(row.get(n)) for n in names])
    return path
