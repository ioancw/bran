"""The roadmap's later sections: the specialist agents, the watchlist they
share, page markers in PDFs, and runs that survive a restart."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bran import resume
from bran.agents import build_options_for, get_agent
from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    get_run,
    insert_run,
    insert_schedule,
    set_setting,
    utcnow_iso,
)
from bran.tools import watchlist
from bran.tools.data import TOOL_NAMES

DATA = "mcp__bran_data__"


def _text(result: dict) -> str:
    return result["content"][0]["text"]


# --- the agents ---------------------------------------------------------------------

def test_the_specialists_load_with_the_tools_their_job_needs():
    filings = get_agent("filings-reader")
    assert {"mcp__bran_docs__read_pdf", DATA + "write_dataset", DATA + "query_dataset"} <= set(filings.tools)
    assert "[page N]" in filings.system_prompt

    quant = get_agent("quant-analyst")
    assert {DATA + "write_dataset", DATA + "query_dataset", DATA + "render_dataset"} <= set(quant.tools)
    assert "no Python" in quant.system_prompt  # it says what it cannot do

    verifier = get_agent("verifier")
    assert {"mcp__bran__get_run_result", DATA + "query_dataset", DATA + "search_briefings"} <= set(verifier.tools)

    monitor = get_agent("watchlist-monitor")
    assert {"mcp__bran_docs__fetch_url", DATA + "get_watchlist", DATA + "query_dataset"} <= set(monitor.tools)

    for agent in (filings, quant, verifier, monitor):
        opts = build_options_for(agent)
        # None of them browses freely, writes files or gets a shell.
        assert {"Bash", "WebSearch", "Write"} <= set(opts.disallowed_tools), agent.name
        assert "bran_data" in agent.mcp_servers
        # Reading the watchlist comes with the data tools; changing it does not.
        assert DATA + "save_watchlist" not in agent.tools, agent.name

    # Only the agent you talk to can change what is watched.
    assert DATA + "save_watchlist" not in TOOL_NAMES and DATA + "get_watchlist" in TOOL_NAMES
    orchestrator = get_agent("orchestrator")
    assert DATA + "save_watchlist" in orchestrator.tools
    for name in ("filings-reader", "quant-analyst", "verifier", "watchlist-monitor"):
        assert f"- `{name}`:" in orchestrator.system_prompt
    for name in ("research", "research-deep", "finance-news"):
        assert DATA + "save_watchlist" not in get_agent(name).tools


def test_the_watchlist_is_read_and_replaced_as_plain_lines():
    set_setting(watchlist.WATCHLIST_KEY, "")
    try:
        empty = _text(asyncio.run(watchlist.get_watchlist.handler({})))
        assert "no watchlist yet" in empty and "USD/JPY above 162" in empty

        saved = asyncio.run(watchlist.save_watchlist.handler(
            {"text": "# my list\n- USD/JPY above 162\n\n* Brent below 65\n  Theme: BoJ intervention  \n"}
        ))
        assert _text(saved).endswith("USD/JPY above 162\nBrent below 65\nTheme: BoJ intervention")
        assert watchlist.read_watchlist() == "USD/JPY above 162\nBrent below 65\nTheme: BoJ intervention"
        assert _text(asyncio.run(watchlist.get_watchlist.handler({}))).startswith("The watchlist:\nUSD/JPY above 162")

        too_long = asyncio.run(watchlist.save_watchlist.handler({"text": "\n".join(f"item {i}" for i in range(60))}))
        assert too_long.get("is_error") is True
        assert watchlist.read_watchlist().count("\n") == 2  # a refused save changes nothing

        cleared = asyncio.run(watchlist.save_watchlist.handler({"text": "  "}))
        assert _text(cleared) == "The watchlist is now empty." and watchlist.read_watchlist() == ""
    finally:
        set_setting(watchlist.WATCHLIST_KEY, "")


def test_pdf_text_is_marked_page_by_page(monkeypatch):
    import pypdf

    from bran.tools import documents

    class Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            if self._text is None:
                raise ValueError("broken stream")
            return self._text

    pages = [Page("Minutes of the meeting"), Page(""), Page(None), Page("Vote: 7-2 to hold at 3.75%")]
    monkeypatch.setattr(pypdf, "PdfReader", lambda _stream: SimpleNamespace(pages=pages))
    text, read, total = documents._extract_text(b"%PDF")
    assert (read, total) == (4, 4)
    assert text.startswith("[page 1]\nMinutes of the meeting")
    assert "[page 2]" not in text  # a blank page adds nothing
    assert "[page 3]\n[error extracting this page: broken stream]" in text
    assert text.endswith("[page 4]\nVote: 7-2 to hold at 3.75%")


# --- runs that a restart cut off -----------------------------------------------------------

def _stranded(
    source: str, *, status: str = "running", hours_ago: float = 0.1, agent: str = "research",
    schedule_id: str | None = None, **meta,
) -> RunRecord:
    r = RunRecord.new(agent=agent, task=f"job {uuid.uuid4().hex[:6]}", source=source, schedule_id=schedule_id)
    r.status = status
    r.started_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    r.metadata.update(meta)
    insert_run(r)
    return r


@pytest.fixture()
def clean_slate():
    """No run in flight before or after (other tests leave some behind)."""
    resume.recover()
    yield
    resume.recover()


def test_a_restart_resumes_background_work_and_fails_the_rest(clean_slate, monkeypatch):
    spawn = _stranded("spawn")
    manual = _stranded("manual", status="pending")
    chat = _stranded("chat")
    inline = _stranded("spawn", inline=True)
    old = _stranded("spawn", hours_ago=resume.MAX_AGE_HOURS + 1)
    twice = _stranded("manual", resumed=1)
    child = _stranded("subagent")
    gone = _stranded("spawn", agent="an-agent-that-was-deleted")
    from bran.persistence import update_run

    sched = ScheduleRecord(
        id=str(uuid.uuid4()), name="resume-" + uuid.uuid4().hex[:6], agent="research", task="brief",
        cron="0 7 * * *", enabled=True, created_at=utcnow_iso(),
    )
    insert_schedule(sched)
    scheduled = _stranded("runner", schedule_id=sched.id)
    again = _stranded("runner", schedule_id=sched.id)

    plan = resume.recover()

    assert {r.id for r in plan.resume} == {spawn.id, manual.id}
    assert plan.refire == [sched.id]  # two cut-off runs of one automation fire it once
    assert plan.failed == 8
    for r in (spawn, manual):
        now = get_run(r.id)
        assert now.status == "pending" and now.metadata["resumed"] == 1 and now.error is None
    for r in (chat, inline, old, twice, child, gone):
        now = get_run(r.id)
        assert now.status == "failed" and now.error == resume.INTERRUPTED and now.ended_at
    for r in (scheduled, again):
        assert get_run(r.id).error == resume.REFIRED

    # Each resumed run is started again: with its session when it had one.
    started: list[tuple[str, str, str | None]] = []

    async def fake_run_agent(agent, prompt, *, record=None, resume_session=None, **kw):
        started.append((record.id, prompt, resume_session))
        record.status = "completed"
        update_run(record)
        return record

    monkeypatch.setattr("bran.runner.run_agent", fake_run_agent)
    monkeypatch.setattr(resume, "_session_to_resume", lambda r: "sess-1" if r.id == spawn.id else None)
    fired: list[list[str]] = []
    monkeypatch.setattr("bran.scheduler.fire_again", lambda ids: fired.append(ids) or len(ids))

    async def go():
        done = resume.start(plan)
        await asyncio.sleep(0.05)
        return done

    assert asyncio.run(go()) == {"resumed": 2, "fired_again": 1, "failed": 8}
    by_id = {rid: (prompt, session) for rid, prompt, session in started}
    assert by_id[spawn.id][1] == "sess-1" and by_id[spawn.id][0].endswith(spawn.task)
    assert "picked up again" in by_id[spawn.id][0]
    assert by_id[manual.id] == (manual.task, None)  # it had not begun: it starts from the top
    assert fired == [[sched.id]]

    # A second restart does not resume the same run again.
    for r in (spawn, manual):
        rec = get_run(r.id)
        rec.status = "running"
        update_run(rec)
    assert resume.recover().resume == []
    assert get_run(spawn.id).status == "failed"


def test_resuming_can_be_switched_off(clean_slate, monkeypatch):
    monkeypatch.setenv("BRAN_RESUME", "0")
    r = _stranded("spawn")
    plan = resume.recover()
    assert plan.resume == [] and plan.refire == [] and get_run(r.id).status == "failed"


def test_an_automation_is_fired_again_unless_catch_up_has_it(monkeypatch):
    import bran.scheduler as sched

    jobs: list[dict] = []

    class FakeScheduler:
        def add_job(self, func, **kw):
            jobs.append(kw)

    on = ScheduleRecord(
        id=str(uuid.uuid4()), name="fire-" + uuid.uuid4().hex[:6], agent="research", task="t",
        cron="0 7 * * *", enabled=True, created_at=utcnow_iso(),
    )
    off = ScheduleRecord(
        id=str(uuid.uuid4()), name="fire-" + uuid.uuid4().hex[:6], agent="research", task="t",
        cron="0 7 * * *", enabled=False, created_at=utcnow_iso(),
    )
    caught = ScheduleRecord(
        id=str(uuid.uuid4()), name="fire-" + uuid.uuid4().hex[:6], agent="research", task="t",
        cron="0 7 * * *", enabled=True, created_at=utcnow_iso(),
    )
    for s in (on, off, caught):
        insert_schedule(s)
    monkeypatch.setattr(sched, "_scheduler", FakeScheduler())
    monkeypatch.setattr(sched, "_startup_report", {"caught_up": [{"name": caught.name, "due": "x"}], "skipped": []})
    assert sched.fire_again([on.id, off.id, caught.id, "no-such-id"]) == 1
    assert jobs[0]["id"] == f"bran-resume:{on.name}" and jobs[0]["args"][2] == on.name
    monkeypatch.setattr(sched, "_scheduler", None)
    assert sched.fire_again([on.id]) == 0
