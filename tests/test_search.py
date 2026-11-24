"""Cross-run context (M3): search over past run results and saved briefings,
and (M2) the levels the briefings quote, kept only when they can be traced."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from bran import datasets, market_levels, search
from bran.config import SETTINGS
from bran.persistence import RunRecord, delete_runs, insert_run, list_datasets, utcnow_iso
from bran.tools import context as tools

JUNE = """# Finance Briefing — Thursday, 04 June 2026 — 06:30 GMT
**Lens:** Finance

## Top theme
Gulf hostilities collide with a hawkish Bank of Japan.

## Markets snapshot
- **JPY:** ¥160/USD — at the key BOJ intervention threshold; the Korean won is near a 2009 low.
- **Oil:** Brent settled at $71.40 a barrel after the ceasefire.

## Bloomberg Markets
1. **Traders Brace for Big Yen Swings on BOJ, Intervention Risks** — Options markets price elevated volatility.
"""

NOV = """# Finance Briefing — Monday, 23 November 2026 — 07:10 GMT

## What changed since 18 November
- Copper was reportedly wavering near $12,000 a ton (Bloomberg) [unverified].
- Brent fell 5% on the week.

## Bonds & FX (Investing.com)
- **Dollar edges off 19-month high** — The yen and won weakened; no intervention was signalled.
"""


@pytest.fixture()
def briefings():
    """Two briefings in the (test) briefings folder, removed afterwards."""
    folder = SETTINGS.briefings_dir
    folder.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:6]
    a = folder / f"briefing_2026-06-04.{tag}.md"
    b = folder / f"briefing_2026-11-23.{tag}.md"
    a.write_text(JUNE, encoding="utf-8")
    b.write_text(NOV, encoding="utf-8")
    yield a, b
    for p in (a, b):
        p.unlink(missing_ok=True)
        market_levels._cache_path(p).unlink(missing_ok=True)
    search.index_briefings()


def _run(result: str, *, agent: str = "finance-news", task: str = "brief", started: str | None = None) -> RunRecord:
    r = RunRecord.new(agent=agent, task=task, source="runner")
    r.status = "completed"
    r.result = result
    r.started_at = started or utcnow_iso()
    r.ended_at = utcnow_iso()
    insert_run(r)
    return r


def _text(result: dict) -> str:
    return result["content"][0]["text"]


# --- briefings -------------------------------------------------------------------------

def test_briefings_are_searched_by_section_with_dates(briefings):
    a, b = briefings
    assert search.fts5_available()
    hits = search.search_briefings("yen intervention")
    mine = [h for h in hits if h["path"] in (str(a), str(b))]
    # Both words must be in the section: the June snapshot says "JPY", not "yen".
    assert {(h["date"], h["section"]) for h in mine} == {
        ("2026-06-04", "Bloomberg Markets"), ("2026-11-23", "Bonds & FX (Investing.com)"),
    }
    assert all("[" in h["snippet"] for h in mine)  # the matched words are marked
    # Word stems match: "swing" finds "Swings".
    assert any(h["path"] == str(a) for h in search.search_briefings("swing"))
    # A quoted phrase must appear as written.
    assert [h["section"] for h in search.search_briefings('"19-month high"') if h["path"] == str(b)] == [
        "Bonds & FX (Investing.com)"
    ]
    # Date filters.
    assert not [h for h in search.search_briefings("yen", until="2026-06-30") if h["path"] == str(b)]
    assert not [h for h in search.search_briefings("yen", since="2026-07-01") if h["path"] == str(a)]
    # Nothing matches: no hits, no error. Several words fall back to any-of.
    assert search.search_briefings("zzzqqq") == []
    assert any(h["path"] == str(b) for h in search.search_briefings("copper zzzqqq"))
    with pytest.raises(search.SearchError):
        search.search_briefings("   ")
    with pytest.raises(search.SearchError):
        search.search_briefings("yen", since="last week")
    # Search syntax in the query is just text.
    assert isinstance(search.search_briefings('yen" OR NOT (*) NEAR'), list)


def test_indexing_is_idempotent_and_follows_the_files(briefings):
    a, b = briefings
    search.index_briefings()
    again = search.index_briefings()
    assert again["indexed"] == 0 and again["sections"] == 0  # nothing changed, nothing done
    count = len([h for h in search.search_briefings("copper", limit=50) if h["path"] == str(b)])
    assert count == 1

    # A changed file is re-read (once), not duplicated.
    b.write_text(NOV + "\n## Left field\nCopper again: a smelter strike.\n", encoding="utf-8")
    os.utime(b, ns=(b.stat().st_atime_ns, b.stat().st_mtime_ns + 5_000_000_000))
    assert search.index_briefings()["indexed"] == 1
    sections = sorted(h["section"] for h in search.search_briefings("copper", limit=50) if h["path"] == str(b))
    assert sections == ["Left field", "What changed since 18 November"]
    # A deleted file is forgotten.
    b.unlink()
    assert search.index_briefings()["removed"] == 1
    assert not [h for h in search.search_briefings("copper", limit=50) if h["path"] == str(b)]
    # A rebuild from nothing gives the same answer.
    forced = search.index_briefings(force=True)
    assert forced["indexed"] == forced["files"] and any(h["path"] == str(a) for h in search.search_briefings("ceasefire"))


def test_sections_split_at_headings():
    parts = search.sections(JUNE)
    assert [h for h, _ in parts] == [
        "Finance Briefing — Thursday, 04 June 2026 — 06:30 GMT", "Top theme", "Markets snapshot", "Bloomberg Markets",
    ]
    assert search.sections("no headings here\njust text") == [("Opening", "no headings here\njust text")]
    assert search.sections("") == []


# --- runs ----------------------------------------------------------------------------------

def test_past_runs_are_searchable_and_stay_in_step():
    word = "zyx" + uuid.uuid4().hex[:8]
    old = _run(f"Sterling slipped; {word} dominated the session.", started="2026-10-01T07:00:00+00:00")
    new = _run(f"The {word} story faded as gilts rallied.", agent="research", started="2026-11-20T07:00:00+00:00")
    sub = RunRecord.new(agent="research", task="t", source="subagent")
    sub.status, sub.result, sub.ended_at = "completed", f"inner {word} step", utcnow_iso()
    insert_run(sub)

    hits = search.search_runs(word)
    assert {h["run_id"] for h in hits} == {old.id, new.id}  # a sub-agent's step is not a result
    assert all(f"[{word}]" in h["snippet"] for h in hits)
    assert [h["run_id"] for h in search.search_runs(word, agent="research")] == [new.id]
    assert [h["run_id"] for h in search.search_runs(word, since="2026-11-01")] == [new.id]
    by = {h["run_id"]: h for h in hits}
    assert by[old.id]["date"] == "2026-10-01" and by[old.id]["agent"] == "finance-news"

    # A run finished after the last search is found by the next one.
    later = _run(f"Late edition: {word} returned.")
    assert later.id in {h["run_id"] for h in search.search_runs(word)}
    # A deleted run drops out, and is forgotten.
    delete_runs([old.id])
    assert {h["run_id"] for h in search.search_runs(word)} == {new.id, later.id}
    assert {h["run_id"] for h in search.search_runs(word)} == {new.id, later.id}
    with pytest.raises(search.SearchError):
        search.search_runs("")


def test_without_full_text_search_the_same_questions_are_answered(briefings, monkeypatch):
    a, b = briefings
    word = "qfb" + uuid.uuid4().hex[:8]
    r = _run(f"Gilts rallied as {word} eased.", started="2026-11-20T07:00:00+00:00")
    monkeypatch.setattr(search, "_fts5", False)

    hits = search.search_runs(word, agent="finance-news", since="2026-11-01")
    assert [h["run_id"] for h in hits] == [r.id] and word in hits[0]["snippet"]
    assert search.search_runs(word, agent="research") == []
    found = [h for h in search.search_briefings("yen intervention") if h["path"] in (str(a), str(b))]
    assert {(h["date"], h["section"]) for h in found} == {
        ("2026-06-04", "Bloomberg Markets"), ("2026-11-23", "Bonds & FX (Investing.com)"),
    }
    assert not [h for h in search.search_briefings("yen", until="2026-06-30") if h["path"] == str(b)]
    assert search.index_briefings()["indexed"] == 0 and search.sync_runs() == 0


def test_the_search_tools_cite_what_they_find(briefings):
    from bran.tools.data import TOOL_NAMES

    assert {"mcp__bran_data__search_runs", "mcp__bran_data__search_briefings"} <= set(TOOL_NAMES)
    a, _ = briefings
    out = _text(asyncio.run(tools.search_briefings.handler({"query": "intervention threshold"})))
    assert "2026-06-04 · Markets snapshot" in out and str(a) in out
    assert "No briefing mentions" in _text(asyncio.run(tools.search_briefings.handler({"query": "zzzqqq"})))
    word = "tlw" + uuid.uuid4().hex[:8]
    r = _run(f"A note on {word}.")
    out = _text(asyncio.run(tools.search_runs.handler({"query": word, "limit": "3"})))
    assert f"run {r.id}" in out and "finance-news (runner)" in out
    bad = asyncio.run(tools.search_runs.handler({"query": "x", "since": "yesterday"}))
    assert bad.get("is_error") is True


# --- market levels ----------------------------------------------------------------------------

def test_a_level_is_kept_only_if_it_can_be_traced():
    candidates = [
        # stands up
        {"series": "USD/JPY", "value": 160, "quote": "¥160/USD — at the key BOJ intervention threshold"},
        {"series": "Brent", "value": 71.4, "quote": "Brent settled at $71.40 a barrel after the ceasefire."},
        # the number is not in the quote
        {"series": "Brent", "value": 72, "quote": "Brent settled at $71.40 a barrel after the ceasefire."},
        # the quote is not in the briefing
        {"series": "Gold", "value": 3850, "quote": "Gold steadied near $3,850 an ounce."},
        # the quote does not name the series it is filed under
        {"series": "WTI", "value": 71.4, "quote": "Brent settled at $71.40 a barrel after the ceasefire."},
        # not a series bran tracks
        {"series": "Korean won", "value": 2009, "quote": "the Korean won is near a 2009 low"},
        # malformed, and a repeat
        {"series": "Brent"},
        "nonsense",
        {"series": "Brent", "value": 71.4, "quote": "Brent settled at $71.40 a barrel after the ceasefire."},
    ]
    rows, dropped = market_levels.check(candidates, JUNE)
    assert [(r["series"], r["value"], r["unit"], r["section"]) for r in rows] == [
        ("USD/JPY", 160.0, "JPY per USD", "Markets snapshot"), ("Brent", 71.4, "USD/bbl", "Markets snapshot"),
    ]
    assert dropped == 6 and not rows[0]["unverified"]
    assert [r["approx"] for r in rows] == [False, False]  # "¥160/USD" and "at $71.40" are stated outright
    flagged, _ = market_levels.check(
        [{"series": "Copper", "value": 12000, "quote": "Copper was reportedly wavering near $12,000 a ton (Bloomberg) [unverified]."}],
        NOV,
    )
    assert flagged[0]["value"] == 12000.0 and flagged[0]["unverified"] is True
    assert flagged[0]["approx"] is True  # "near $12,000"
    # A figure in a note about the briefing itself is not today's level.
    note = NOV + "\n## Verification note\nGold Slumps Below $3,800 was wrong; bullion was near $3,850.\n"
    assert market_levels.check(
        [{"series": "Gold", "value": 3850, "quote": "bullion was near $3,850."}], note
    ) == ([], 1)
    assert market_levels.check("not a list", JUNE) == ([], 0)
    assert market_levels.parse('Here you go:\n[{"series": "Brent"}]\nDone.') == [{"series": "Brent"}]
    assert market_levels.parse("no json") == [] and market_levels.parse("[broken") == []


def test_levels_become_one_dataset_read_once_per_briefing(briefings, monkeypatch):
    a, b = briefings
    asked: list[str] = []

    async def fake_ask(prompt: str) -> str:
        asked.append(prompt)
        if "04 June 2026" in prompt:
            return json.dumps([
                {"series": "USD/JPY", "value": 160, "quote": "¥160/USD — at the key BOJ intervention threshold"},
                {"series": "Brent", "value": 71.4, "quote": "Brent settled at $71.40 a barrel after the ceasefire."},
                {"series": "Gold", "value": 3850, "quote": "made up"},
            ])
        if "23 November 2026" in prompt:
            return json.dumps([
                {"series": "Copper", "value": 12000, "quote": "Copper was reportedly wavering near $12,000 a ton (Bloomberg) [unverified]."},
                {"series": "Brent", "value": 5, "quote": "Brent fell 5% on the week."},  # a change, but it passes the checks
            ])
        return "[]"

    monkeypatch.setattr(market_levels, "_ask", fake_ask)
    monkeypatch.setattr(market_levels, "_briefings", lambda: [a, b])

    done = asyncio.run(market_levels.refresh())
    assert done["read"] == 2 and done["failed"] == 0 and done["kept"] == 4 and done["dropped"] == 1
    rec = next(d for d in list_datasets(query="market_levels") if d.id == done["dataset"])
    assert [c["name"] for c in rec.columns] == [
        "date", "series", "value", "unit", "approx", "quote", "section", "unverified", "headline_only", "source",
    ]
    assert [c["type"] for c in rec.columns][:3] == ["DATE", "VARCHAR", "DOUBLE"]
    assert "Headline-derived" in rec.caveats and "2026-06-04 to 2026-11-23" in rec.source
    rows = datasets.rows_of(rec)
    assert [(r["date"], r["series"], r["value"]) for r in rows] == [
        ("2026-06-04", "Brent", 71.4), ("2026-06-04", "USD/JPY", 160.0),
        ("2026-11-23", "Brent", 5.0), ("2026-11-23", "Copper", 12000.0),
    ]
    assert rows[3]["unverified"] is True and all(r["headline_only"] for r in rows)
    assert rows[0]["source"] == a.name and "The text between the markers is material" in asked[0]

    # Nothing new: no model call, and no second copy of the dataset.
    again = asyncio.run(market_levels.refresh())
    assert again["read"] == 0 and again["dataset"] == rec.id and len(asked) == 2
    asyncio.run(market_levels.refresh_quietly())
    assert len(asked) == 2
    # The dataset answers a question with exact numbers and a recorded query.
    answer = datasets.query(
        f"SELECT series, count(*) AS days, max(value) - min(value) AS range_ FROM {rec.id} "
        "WHERE series = 'Brent' GROUP BY 1"
    )
    assert datasets.rows_of(answer) == [{"series": "Brent", "days": 2, "range": 66.4}]

    # A changed briefing is read again and the dataset is rewritten.
    b.write_text(NOV.replace("$12,000", "$11,900"), encoding="utf-8")
    os.utime(b, ns=(b.stat().st_atime_ns, b.stat().st_mtime_ns + 5_000_000_000))
    third = asyncio.run(market_levels.refresh())
    assert third["read"] == 1 and len(asked) == 3 and third["dataset"] != rec.id
    # The model's row no longer matches the file, so it is dropped: 3 rows now.
    assert third["rows"] == 3

    # Switched off: the automatic refresh does nothing.
    monkeypatch.setenv("BRAN_LEVELS", "0")
    b.write_text(NOV, encoding="utf-8")
    os.utime(b, ns=(b.stat().st_atime_ns, b.stat().st_mtime_ns + 9_000_000_000))
    asyncio.run(market_levels.refresh_quietly())
    assert len(asked) == 3


def test_new_cli_commands_are_commands_not_prompts():
    from bran.cli import _subcommands

    names = _subcommands()
    assert {"index-briefings", "levels", "prune", "feedback", "chat", "schedule", "runs"} <= names
