"""Dataset MCP tools: hand tables between agents by handle, compute by SQL.

The rule these tools exist for: a model never re-types a table. It writes the
table once (`write_dataset`), passes the handle and the small manifest it got
back, and uses `query_dataset` for any join, comparison or arithmetic. See
`bran.datasets` for the engine and its safety properties.

Own least-privilege server: tools become `mcp__bran_data__<name>`, and an
agent has them only if its file lists them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}], "is_error": True}


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


@tool(
    "write_dataset",
    (
        "Store a table ONCE and get back a handle (like ds_a1f3c9) and a small manifest: "
        "columns with types, row count, the first rows. From then on pass the handle and the "
        "manifest, never the rows. Give `rows` (a JSON array of objects, one per row, column "
        "name to value) OR `csv_path` (a CSV inside this project's working folder or bran's "
        "home). `source` is required: where the numbers came from and their as-of date. Put "
        "anything a reader must be careful about in `caveats` (e.g. 'headline only, not "
        "checked against the article'). Use real types: numbers as numbers, dates as "
        "YYYY-MM-DD, missing values as null. Column names are tidied so SQL can use them "
        "without quoting; the manifest shows the names to use."
    ),
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "A few words saying what the table is."},
            "rows": {
                "type": "array", "items": {"type": "object"},
                "description": "The table: one object per row.",
            },
            "csv_path": {"type": "string", "description": "A CSV file to read instead of `rows`."},
            "source": {"type": "string", "description": "Where the numbers came from, and as of when."},
            "description": {"type": "string"},
            "caveats": {"type": "string"},
        },
        "required": ["name", "source"],
    },
)
async def write_dataset(args: dict[str, Any]) -> dict[str, Any]:
    from bran.datasets import DatasetError, manifest, write

    try:
        record = await asyncio.to_thread(
            write, args.get("name") or "", rows=args.get("rows"),
            csv_path=(args.get("csv_path") or "").strip(), source=args.get("source") or "",
            description=args.get("description") or "", caveats=args.get("caveats") or "",
        )
    except DatasetError as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001 — an agent gets a sentence, not a traceback
        return _err(f"couldn't store the dataset: {e}")
    return _ok(manifest(record))


@tool(
    "dataset_head",
    (
        "Show a dataset's manifest: its columns with types, row count, source, caveats, how it "
        "was derived, and its first `n` rows (default 5, at most 50). Use it to look at a "
        "dataset another agent handed you by handle."
    ),
    {
        "type": "object",
        "properties": {"dataset_id": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["dataset_id"],
    },
)
async def dataset_head(args: dict[str, Any]) -> dict[str, Any]:
    from bran.datasets import PREVIEW_ROWS, DatasetError, manifest, require

    try:
        record = require(args.get("dataset_id") or "")
    except DatasetError as e:
        return _err(str(e))
    return _ok(manifest(record, _int(args.get("n"), PREVIEW_ROWS, 1, 50)))


@tool(
    "list_datasets",
    (
        "List stored datasets, newest first: handle, name, rows, when it was made and its "
        "source. Datasets persist across runs, so this is how you find a table an earlier run "
        "produced. `query` filters by a word in the name or description; `project_only` keeps "
        "to the current project; `limit` defaults to 20."
    ),
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "project_only": {"type": "boolean"},
            "limit": {"type": "integer"},
        },
    },
)
async def list_datasets(args: dict[str, Any]) -> dict[str, Any]:
    from bran.background import current_project_id
    from bran.persistence import list_datasets as stored

    project_id = current_project_id.get() if _flag(args.get("project_only")) else None
    if _flag(args.get("project_only")) and not project_id:
        return _ok("This run is not in a project, so there are no project datasets to list.")
    rows = stored(
        query=(args.get("query") or "").strip() or None, project_id=project_id,
        limit=_int(args.get("limit"), 20, 1, 100),
    )
    if not rows:
        return _ok("No matching datasets.")
    lines = [
        f"- {r.id}  {r.name}  ({r.row_count:,} rows, {len(r.columns)} columns, "
        f"{r.created_at[:10]})  source: {r.source[:80]}"
        for r in rows
    ]
    return _ok("Datasets:\n" + "\n".join(lines))


@tool(
    "query_dataset",
    (
        "Run ONE SQL SELECT (DuckDB dialect) over stored datasets and get back the manifest of "
        "a NEW dataset holding the result. Name datasets by handle, e.g. "
        "`SELECT a.date, a.value - b.value AS spread FROM ds_a1f3c9 a JOIN ds_b7c2d1 b USING "
        "(date)`. Use this for every join, filter, aggregate, comparison and calculation: the "
        "engine does the arithmetic exactly, and the result records the SQL and the datasets "
        "it came from. Only a single SELECT (or WITH ... SELECT) is allowed; it cannot read "
        "files or the network, or change anything. Results are capped at 100,000 rows and 20 "
        "seconds. `name` labels the result."
    ),
    {
        "type": "object",
        "properties": {"sql": {"type": "string"}, "name": {"type": "string"}},
        "required": ["sql"],
    },
)
async def query_dataset(args: dict[str, Any]) -> dict[str, Any]:
    from bran.datasets import DatasetError, manifest, query

    try:
        record = await asyncio.to_thread(query, args.get("sql") or "", args.get("name") or "")
    except DatasetError as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001
        return _err(f"the query could not be run: {e}")
    return _ok(manifest(record))


@tool(
    "render_dataset",
    (
        "Produce a dataset for the user from its handle. `format` 'markdown' returns the table "
        "as markdown text to put in your reply (up to `limit` rows, default 50, at most 500). "
        "`format` 'csv' saves the WHOLE dataset as a CSV in the project's Files (or bran's "
        "documents folder outside a project) and returns its path. This is the only way a "
        "table should reach a reply: never copy rows out of a manifest by hand."
    ),
    {
        "type": "object",
        "properties": {
            "dataset_id": {"type": "string"},
            "format": {"type": "string", "enum": ["markdown", "csv"]},
            "limit": {"type": "integer"},
        },
        "required": ["dataset_id"],
    },
)
async def render_dataset(args: dict[str, Any]) -> dict[str, Any]:
    from bran.datasets import DatasetError, export_csv, markdown_table, require, rows_of

    try:
        record = require(args.get("dataset_id") or "")
    except DatasetError as e:
        return _err(str(e))
    fmt = (args.get("format") or "markdown").strip().lower()
    if fmt == "csv":
        try:
            path = await asyncio.to_thread(export_csv, record)
        except Exception as e:  # noqa: BLE001
            return _err(f"couldn't save the CSV: {e}")
        return _ok(f"Saved {record.row_count:,} rows of {record.id} ({record.name}) to {path}")
    if fmt != "markdown":
        return _err("`format` must be 'markdown' or 'csv'.")
    limit = _int(args.get("limit"), 50, 1, 500)
    table = markdown_table(record, rows_of(record, limit), cell_limit=200)
    note = ""
    if record.row_count > limit:
        note = (
            f"\n\n(First {limit:,} of {record.row_count:,} rows. Use format 'csv' for the whole "
            "table, or query_dataset to narrow it.)"
        )
    return _ok(table + note)


DATA_TOOLS = [write_dataset, dataset_head, list_datasets, query_dataset, render_dataset]

# The cross-run search tools (bran.tools.context) ride on the same server: an
# agent that works with data also needs to find what was produced before.
from bran.tools.context import CONTEXT_TOOLS  # noqa: E402
from bran.tools.watchlist import WATCHLIST_TOOLS, save_watchlist  # noqa: E402

ALL_TOOLS = [*DATA_TOOLS, *CONTEXT_TOOLS, *WATCHLIST_TOOLS]
# What `mcp__bran_data__*` in an agent file grants. Changing the watchlist is
# left out: only an agent that lists save_watchlist by name can do that, so an
# agent that reads the web cannot be talked into rewriting what you watch.
TOOL_NAMES = [f"mcp__bran_data__{t.name}" for t in ALL_TOOLS if t is not save_watchlist]
data_server = create_sdk_mcp_server(name="bran_data", version="0.1.0", tools=ALL_TOOLS)
