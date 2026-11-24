"""The data layer (M1): datasets stored once and passed by handle, queried by
SQL in a locked-down engine, with provenance on everything derived."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from bran import datasets
from bran.background import current_project_id, current_run_id
from bran.config import SETTINGS
from bran.datasets import DatasetError
from bran.persistence import ProjectRecord, get_dataset, insert_project, list_datasets
from bran.tools import data as tools

LEVELS = [
    {"Date": "2026-11-20", "Series": "USD/JPY", "Value": 154.2, "Headline only": False, "n": 3},
    {"Date": "2026-11-21", "Series": "USD/JPY", "Value": 155, "Headline only": False, "n": 4},
    {"Date": "2026-11-20", "Series": "Brent", "Value": 71.4, "Headline only": True, "n": None},
    {"Date": "2026-11-21", "Series": "Brent", "Value": 70.9, "Headline only": True, "n": 1},
]


def _levels(**kw):
    return datasets.write("market levels", rows=LEVELS, source="briefing 2026-11-21", **kw)


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _call(tool, **args) -> dict:
    return asyncio.run(tool.handler(args))


# --- writing and reading ---------------------------------------------------------

def test_a_table_is_stored_once_and_read_back_exactly():
    rec = _levels(description="daily closes", caveats="Brent is headline only")
    assert datasets.HANDLE.fullmatch(rec.id) and rec.row_count == 4
    # Names are tidied so SQL needs no quoting; types are the columns' own.
    assert rec.columns == [
        {"name": "date", "type": "DATE"}, {"name": "series", "type": "VARCHAR"},
        {"name": "value", "type": "DOUBLE"}, {"name": "headline_only", "type": "BOOLEAN"},
        {"name": "n", "type": "BIGINT"},
    ]
    again = get_dataset(rec.id)
    assert again == rec
    rows = datasets.rows_of(again)
    assert rows[0] == {"date": "2026-11-20", "series": "USD/JPY", "value": 154.2, "headline_only": False, "n": 3}
    assert rows[1]["value"] == 155.0 and rows[2]["n"] is None
    assert datasets.rows_of(again, 2) == rows[:2]
    assert rec.id in [d.id for d in list_datasets(query="MARKET lev")]
    assert rec.id not in [d.id for d in list_datasets(query="no such name")]


def test_types_are_inferred_from_the_values():
    infer = datasets.infer_type
    assert infer([1, 2, None]) == "BIGINT"
    assert infer([1, 2.5]) == "DOUBLE"
    assert infer([True, False]) == "BOOLEAN"
    assert infer([True, 1]) == "VARCHAR"  # a flag is not a number
    assert infer(["2026-11-22", None]) == "DATE"
    assert infer(["2026-11-22", "2026-11-22T09:00:00Z"]) == "TIMESTAMP"
    assert infer(["2026-11-22 09:00", "2026-11-22T09:00:00+00:00"]) == "TIMESTAMP"
    assert infer(["2026-13-45"]) == "VARCHAR"  # shaped like a date, is not one
    assert infer(["5", "6"]) == "VARCHAR"  # text stays text: honest types
    assert infer([1, "n/a"]) == "VARCHAR"
    assert infer([None, None]) == "VARCHAR" and infer([], hint="DOUBLE") == "DOUBLE"
    assert infer([2**70]) == "DOUBLE"

    # Mixed columns are stored as text; nested values as their JSON; NaN as missing.
    rec = datasets.write(
        "odd", source="test",
        rows=[{"a": 1, "b": {"x": 1}, "c": float("nan"), "order": 1, "2nd": "x", "A": True},
              {"a": "n/a", "b": [1, 2], "c": 2.0, "order": 2, "2nd": "y", "A": False}],
    )
    assert [c["name"] for c in rec.columns] == ["a", "b", "c", "order_", "c_2nd", "a_2"]
    assert [c["type"] for c in rec.columns] == ["VARCHAR", "VARCHAR", "DOUBLE", "BIGINT", "VARCHAR", "BOOLEAN"]
    rows = datasets.rows_of(rec)
    assert rows[0]["a"] == "1" and rows[0]["b"] == '{"x": 1}' and rows[0]["c"] is None


def test_the_manifest_is_small_and_says_what_is_true():
    rec = _levels(caveats="Brent is headline only")
    text = datasets.manifest(rec)
    assert f"Dataset {rec.id}: market levels" in text
    assert "Rows: 4. Columns (5): date DATE, series VARCHAR, value DOUBLE, headline_only BOOLEAN, n BIGINT" in text
    assert "Source: briefing 2026-11-21" in text and "Caveats: Brent is headline only" in text
    assert "All rows:" in text and "| 2026-11-20 | USD/JPY | 154.2 | false | 3 |" in text
    assert "Derived from" not in text and "SQL:" not in text

    big = datasets.write(
        "big", source="test",
        rows=[{"i": i, "text": "word " * 400 + "| pipe"} for i in range(300)],
    )
    text = datasets.manifest(big)
    assert "First 5 rows:" in text and text.count("\n| ") == 7  # header, rule, 5 rows
    assert len(text) < 1500  # a long cell is cut; the manifest stays a manifest
    assert "First 12 rows:" in datasets.manifest(big, 12)


def test_what_is_not_a_table_is_refused(monkeypatch):
    with pytest.raises(DatasetError, match="source"):
        datasets.write("x", rows=LEVELS, source="  ")
    with pytest.raises(DatasetError, match="name"):
        datasets.write("", rows=LEVELS, source="s")
    with pytest.raises(DatasetError, match="exactly one"):
        datasets.write("x", source="s")
    with pytest.raises(DatasetError, match="exactly one"):
        datasets.write("x", rows=LEVELS, csv_path="a.csv", source="s")
    with pytest.raises(DatasetError, match="not an object"):
        datasets.write("x", rows=[1, 2], source="s")
    with pytest.raises(DatasetError, match="not valid JSON"):
        datasets.write("x", rows="[{", source="s")
    with pytest.raises(DatasetError, match="documents"):
        datasets.write("x", rows=[{"a": "y" * 10_001}], source="s")
    monkeypatch.setattr(datasets, "MAX_ROWS", 3)
    with pytest.raises(DatasetError, match="more than the 3"):
        datasets.write("x", rows=LEVELS, source="s")
    # Rows given as JSON text are accepted (a model may send them that way).
    monkeypatch.setattr(datasets, "MAX_ROWS", 100)
    assert datasets.write("x", rows=json.dumps(LEVELS), source="s").row_count == 4


# --- provenance --------------------------------------------------------------------

def test_the_producing_run_and_project_are_stamped_not_supplied():
    run_token = current_run_id.set("run-data-1")
    proj_token = current_project_id.set("proj-data-1")
    try:
        rec = _levels()
        derived = datasets.query(f"SELECT series, max(value) AS high FROM {rec.id} GROUP BY 1")
        # Through the tool, an id the model passes is ignored: there is no such field.
        out = _call(tools.write_dataset, name="t", rows=[{"a": 1}], source="s", run_id="forged", project_id="forged")
        made = get_dataset(datasets.HANDLE.search(_text(out)).group(0))
    finally:
        current_run_id.reset(run_token)
        current_project_id.reset(proj_token)
    assert (rec.run_id, rec.project_id) == ("run-data-1", "proj-data-1")
    assert (derived.run_id, derived.project_id) == ("run-data-1", "proj-data-1")
    assert (made.run_id, made.project_id) == ("run-data-1", "proj-data-1")
    assert "by run run-data" in datasets.manifest(rec)
    assert _levels().run_id is None  # outside a run there is nothing to stamp
    assert rec.id in [d.id for d in list_datasets(project_id="proj-data-1")]


def test_a_derived_dataset_records_its_sql_and_parents():
    levels = _levels(caveats="Brent is headline only")
    fx = datasets.write(
        "fx notes", source="desk", caveats="indicative",
        rows=[{"series": "USD/JPY", "desk": "G10"}, {"series": "Brent", "desk": "Commodities"}],
    )
    sql = (
        f"SELECT l.series, f.desk, round(max(l.value) - min(l.value), 2) AS range_, count(*) AS days, "
        f"max(l.date) AS last_date FROM {levels.id} l JOIN {fx.id} f USING (series) "
        "GROUP BY 1, 2 ORDER BY 1;"
    )
    out = datasets.query(sql, "ranges")
    assert out.name == "ranges" and out.source == "query"
    assert out.parent_ids == sorted([levels.id, fx.id]) and out.sql == sql.rstrip(";")
    # Caveats travel with the numbers (in handle order, which is arbitrary).
    assert sorted(out.caveats.split("; ")) == ["Brent is headline only", "indicative"]
    assert out.columns == [
        {"name": "series", "type": "VARCHAR"}, {"name": "desk", "type": "VARCHAR"},
        {"name": "range", "type": "DOUBLE"}, {"name": "days", "type": "BIGINT"},
        {"name": "last_date", "type": "DATE"},
    ]
    # Exact arithmetic, done by the engine.
    assert datasets.rows_of(out) == [
        {"series": "Brent", "desk": "Commodities", "range": 0.5, "days": 2, "last_date": "2026-11-21"},
        {"series": "USD/JPY", "desk": "G10", "range": 0.8, "days": 2, "last_date": "2026-11-21"},
    ]
    text = datasets.manifest(out)
    assert f"Derived from: {', '.join(sorted([levels.id, fx.id]))}" in text and "SQL: SELECT l.series" in text
    # Handles chain: a result is queried like any dataset.
    again = datasets.query(f"SELECT sum(days) AS total FROM {out.id}")
    assert again.parent_ids == [out.id] and datasets.rows_of(again) == [{"total": 4}]
    # An empty result is still a dataset, with its columns typed.
    none = datasets.query(f"SELECT date, value FROM {levels.id} WHERE value > 1000")
    assert none.row_count == 0 and [c["type"] for c in none.columns] == ["DATE", "DOUBLE"]
    assert "(no rows)" in datasets.manifest(none)
    # Timestamps survive the round trip through the engine.
    ts = datasets.write("ts", source="s", rows=[{"at": "2026-11-22T09:00:00Z"}, {"at": "2026-11-23 07:00"}])
    assert ts.columns == [{"name": "at_", "type": "TIMESTAMP"}]  # `at` is a word SQL keeps for itself
    late = datasets.query(f"SELECT max(at_) AS latest, date_diff('hour', min(at_), max(at_)) AS hours FROM {ts.id}")
    assert datasets.rows_of(late) == [{"latest": "2026-11-23T07:00:00", "hours": 22}]
    # Arithmetic alone needs no dataset.
    assert datasets.rows_of(datasets.query("SELECT 17 * 23 AS product, 7 / 2 AS half")) == [{"product": 391, "half": 3.5}]


# --- the engine cannot be turned on the machine --------------------------------------

HOSTILE = [
    # anything but one SELECT
    "DROP TABLE {h}",
    "DELETE FROM {h}",
    "UPDATE {h} SET value = 0",
    "INSERT INTO {h} SELECT * FROM {h}",
    "CREATE TABLE z AS SELECT * FROM {h}",
    "ALTER TABLE {h} ADD COLUMN x INT",
    "SELECT 1; DROP TABLE {h}",
    "SELECT * FROM {h}; SELECT 2",
    "/* fine */ SELECT 1; -- then\nDELETE FROM {h}",
    "COPY {h} TO '/tmp/bran-leak.csv'",
    "COPY (SELECT * FROM {h}) TO '/tmp/bran-leak.csv'",
    "EXPORT DATABASE '/tmp/bran-dump'",
    "ATTACH '{db}' AS s",
    "ATTACH '{db}' AS s (TYPE sqlite)",
    "INSTALL httpfs",
    "LOAD httpfs",
    "PRAGMA database_list",
    "PRAGMA version",
    "SET enable_external_access = true",
    "SET memory_limit = '64GB'",
    "RESET enable_external_access",
    "CALL pragma_version()",
    "EXPLAIN SELECT * FROM {h}",
    "CHECKPOINT",
    "VACUUM",
    # a SELECT reaching for files, the network or the machine
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_csv_auto('/etc/hostname')",
    "SELECT * FROM read_text('/etc/hostname')",
    "SELECT * FROM read_blob('/etc/hostname')",
    "SELECT * FROM read_json('/etc/hostname')",
    "SELECT * FROM read_parquet('https://example.com/x.parquet')",
    "SELECT * FROM '/etc/passwd'",
    "SELECT * FROM '{db}'",
    "SELECT * FROM glob('/home/*')",
    "SELECT * FROM sqlite_scan('{db}', 'runs')",
    "SELECT * FROM duckdb_settings()",
    "SELECT * FROM duckdb_extensions()",
    "SELECT * FROM pragma_database_list()",
    "SELECT current_setting('home_directory')",
    "SELECT * FROM information_schema.tables",
    "SELECT getenv('HOME')",
    # bran's own tables are simply not there
    "SELECT * FROM runs",
    "SELECT * FROM app_settings",
    "SELECT * FROM chats",
    "SELECT * FROM datasets",
    "SELECT * FROM dataset_rows",
]


def test_hostile_sql_is_refused_and_changes_nothing(tmp_path):
    rec = _levels()
    before = datasets.rows_of(rec)
    count = len(list_datasets(limit=100_000))
    leak = Path("/tmp/bran-leak.csv")
    for template in HOSTILE:
        sql = template.format(h=rec.id, db=SETTINGS.db_path)
        with pytest.raises(DatasetError):
            datasets.query(sql)
    assert datasets.rows_of(rec) == before and get_dataset(rec.id) == rec
    assert len(list_datasets(limit=100_000)) == count  # a refused query stores nothing
    assert not leak.exists() and not Path("/tmp/bran-dump").exists()
    # The refusal tells the model what is allowed.
    with pytest.raises(DatasetError, match="Only a single SELECT"):
        datasets.query(f"DROP TABLE {rec.id}")
    with pytest.raises(DatasetError, match="exactly one"):
        datasets.query("SELECT 1; SELECT 2")
    with pytest.raises(DatasetError, match="SQL error"):
        datasets.query(f"SELECT nope FROM {rec.id}")
    with pytest.raises(DatasetError, match="did not parse"):
        datasets.query("SELEC 1")
    with pytest.raises(DatasetError, match="no dataset 'ds_000000'"):
        datasets.query("SELECT * FROM ds_000000")


def test_a_query_is_bounded_in_rows_and_time(monkeypatch):
    with pytest.raises(DatasetError, match="was stopped"):
        datasets.query("SELECT count(*) FROM range(100000000000) a, range(1000) b", timeout_s=0.5)
    monkeypatch.setattr(datasets, "MAX_ROWS", 50)
    with pytest.raises(DatasetError, match="more than 50 rows"):
        datasets.query("SELECT * FROM range(51)")
    assert datasets.query("SELECT * FROM range(50)").row_count == 50


# --- reading a CSV -------------------------------------------------------------------

def test_a_csv_is_read_only_from_where_the_run_may_write(tmp_path):
    inside = SETTINGS.bran_home / "levels-test.csv"
    inside.write_text(
        "﻿Date,Series,Value,Flag,Note\n2026-11-20,Brent,71.4,true,\n2026-11-21,Brent,70,false,\"a, b\"\n\n",
        encoding="utf-8",
    )
    rec = datasets.write("from csv", csv_path=str(inside), source="file")
    assert [(c["name"], c["type"]) for c in rec.columns] == [
        ("date", "DATE"), ("series", "VARCHAR"), ("value", "DOUBLE"), ("flag", "BOOLEAN"), ("note", "VARCHAR"),
    ]
    assert datasets.rows_of(rec)[1] == {
        "date": "2026-11-21", "series": "Brent", "value": 70.0, "flag": False, "note": "a, b",
    }

    outside = tmp_path / "secret.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    for bad in (str(outside), "/etc/passwd", str(SETTINGS.bran_home / ".." / "x.csv"), "../../etc/passwd"):
        with pytest.raises(DatasetError, match="inside bran's home"):
            datasets.write("x", csv_path=bad, source="s")
    with pytest.raises(DatasetError, match="no file"):
        datasets.write("x", csv_path=str(SETTINGS.bran_home / "missing.csv"), source="s")
    (SETTINGS.bran_home / "empty-test.csv").write_text("a,b\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="no rows"):
        datasets.write("x", csv_path=str(SETTINGS.bran_home / "empty-test.csv"), source="s")


# --- the tools an agent calls ------------------------------------------------------------

def test_the_tools_trade_manifests_never_rows():
    from bran.agents import servers_for

    assert "bran_data" in servers_for(["mcp__bran_data__query_dataset"])
    assert [t.name for t in tools.DATA_TOOLS] == [
        "write_dataset", "dataset_head", "list_datasets", "query_dataset", "render_dataset",
    ]

    proj = ProjectRecord.new(name="Data layer project")
    insert_project(proj)
    token = current_project_id.set(proj.id)
    try:
        made = _call(tools.write_dataset, name="market levels", rows=LEVELS, source="briefing 2026-11-21")
        assert "is_error" not in made
        handle = datasets.HANDLE.search(_text(made)).group(0)
        assert f"Dataset {handle}: market levels" in _text(made) and "Rows: 4." in _text(made)

        head = _text(_call(tools.dataset_head, dataset_id=handle, n=2))
        assert "First 2 rows:" in head and "Brent" not in head

        listed = _text(_call(tools.list_datasets, project_only=True))
        assert f"- {handle}  market levels  (4 rows, 5 columns" in listed
        assert _text(_call(tools.list_datasets, query="zzz-nothing")) == "No matching datasets."

        result = _call(tools.query_dataset, sql=f"SELECT series, avg(value) AS mean FROM {handle} GROUP BY 1 ORDER BY 1", name="means")
        derived = datasets.HANDLE.search(_text(result)).group(0)
        assert derived != handle and f"Derived from: {handle}" in _text(result)

        table = _text(_call(tools.render_dataset, dataset_id=derived))
        assert table.splitlines() == ["| series | mean |", "| --- | --- |", "| Brent | 71.15 |", "| USD/JPY | 154.6 |"]
        clipped = _text(_call(tools.render_dataset, dataset_id=handle, limit=1))
        assert "(First 1 of 4 rows." in clipped

        saved = _text(_call(tools.render_dataset, dataset_id=derived, format="csv"))
        path = Path(saved.rsplit(" to ", 1)[1])
        from bran.project_files import files_dir

        assert path.parent == files_dir(proj.id) and path.name == f"means-{derived}.csv"
        assert path.read_text(encoding="utf-8").splitlines() == ["series,mean", "Brent,71.15", "USD/JPY,154.6"]
    finally:
        current_project_id.reset(token)

    # Mistakes come back as errors the model can act on.
    for bad in (
        _call(tools.write_dataset, name="x", rows=LEVELS),
        _call(tools.dataset_head, dataset_id="ds_zzzzzz"),
        _call(tools.query_dataset, sql=f"DELETE FROM {handle}"),
        _call(tools.render_dataset, dataset_id=handle, format="xlsx"),
        _call(tools.render_dataset, dataset_id="../../etc/passwd"),
    ):
        assert bad.get("is_error") is True and _text(bad).startswith("Error: ")
    assert "not in a project" in _text(_call(tools.list_datasets, project_only=True))


# --- an existing database gains the tables -------------------------------------------------

def test_an_existing_database_gains_the_dataset_tables_and_keeps_its_data(tmp_path):
    from bran.persistence import _MIGRATIONS, _TABLES, _bring_up_to_date

    path = tmp_path / "v5.sqlite"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # A database as it was before this change: everything but the dataset tables.
    before = _TABLES.split("-- Datasets:")[0]
    assert "CREATE TABLE IF NOT EXISTS datasets" not in before
    conn.executescript(before)
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('user_instructions', 'keep me')")
    conn.execute(
        "INSERT INTO chats (id, title, agent, created_at, updated_at) VALUES ('c1', 't', 'orchestrator', 'x', 'x')"
    )
    conn.execute("PRAGMA user_version = 5")

    _bring_up_to_date(conn)

    assert len(_MIGRATIONS) == 6 and conn.execute("PRAGMA user_version").fetchone()[0] == 6
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"datasets", "dataset_rows"} <= tables
    assert conn.execute("SELECT value FROM app_settings WHERE key = 'user_instructions'").fetchone()[0] == "keep me"
    assert conn.execute("SELECT count(*) FROM chats").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM datasets").fetchone()[0] == 0
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_datasets_created", "idx_datasets_project"} <= indexes
    # A second start changes nothing.
    conn.execute("INSERT INTO datasets (id, name, columns, row_count, created_at) VALUES ('ds_aaaaaa', 'n', '[]', 0, 'x')")
    _bring_up_to_date(conn)
    assert conn.execute("SELECT count(*) FROM datasets").fetchone()[0] == 1
    conn.close()
