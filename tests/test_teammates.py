"""Teammates: an agent with a name, a standing brief, one chat and its own
automations. The chat and the scheduled runs are separate sessions joined by
the system prompt — these tests pin down what each is told."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import bran.web.spa_routes.chats as spa
from bran.api import build_app
from bran.persistence import (
    ChatRecord,
    RunRecord,
    get_schedule,
    get_teammate,
    insert_run,
    list_chats,
    list_schedules,
    upsert_chat,
    utcnow_iso,
)
from bran.schedule_service import create_schedule
from bran.teammates import TeammateError, chat_context, create_teammate, run_context

SAME = {"sec-fetch-site": "same-origin"}


def _name(prefix: str = "mate") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _run(**kw) -> RunRecord:
    r = RunRecord.new(agent=kw.pop("agent", "orchestrator"), task=kw.pop("task", "t"),
                      source=kw.pop("source", "runner"))
    r.status = kw.pop("status", "completed")
    r.ended_at = utcnow_iso()
    for k, v in kw.items():
        setattr(r, k, v)
    insert_run(r)
    return r


@pytest.fixture(scope="module")
def client():
    with TestClient(build_app(enable_scheduler=False)) as c:
        yield c


def test_create_with_a_schedule_makes_its_automation():
    name = _name("Macro Desk")
    t = create_teammate(name=name, agent="orchestrator", brief="Brief me on rates.",
                        schedule="every weekday at 7am")
    autos = [s for s in list_schedules()
             if s.teammate_id == t.id]
    assert len(autos) == 1
    assert autos[0].cron == "0 7 * * mon-fri" and autos[0].task == "Brief me on rates."
    assert autos[0].agent == "orchestrator"
    assert autos[0].name.startswith("macro-desk-")


def test_create_rejects_what_cannot_work_and_stores_nothing():
    name = _name()
    with pytest.raises(TeammateError):
        create_teammate(name=name, agent="no-such-agent")
    with pytest.raises(TeammateError):
        create_teammate(name=name, agent="orchestrator", brief="x", schedule="whenever you fancy")
    with pytest.raises(TeammateError):  # a schedule with nothing to run
        create_teammate(name=name, agent="orchestrator", schedule="every day at 8am")
    create_teammate(name=name, agent="orchestrator")  # the name is still free
    with pytest.raises(TeammateError) as dup:
        create_teammate(name=name, agent="orchestrator")
    assert dup.value.kind == "conflict"


def test_adopting_an_automation_and_deleting_the_teammate_leaves_it_running(client):
    auto = _name("auto")
    create_schedule(name=auto, agent="orchestrator", task="do it", cron="0 9 * * *")
    made = client.post("/spa/teammates", headers=SAME,
                       data={"name": _name(), "agent": "orchestrator", "adopt": auto}).json()
    assert [a["name"] for a in made["automations"]] == [auto]
    assert made["state"] == "ready" and made["next_run"]
    # Another teammate cannot take it.
    again = client.post("/spa/teammates", headers=SAME,
                        data={"name": _name(), "agent": "orchestrator", "adopt": auto})
    assert again.status_code == 409
    # Hand it back, then give it again.
    back = client.post(f"/spa/teammates/{made['id']}/automations", headers=SAME,
                       data={"name": auto, "attach": "false"}).json()
    assert back["automations"] == []
    client.post(f"/spa/teammates/{made['id']}/automations", headers=SAME, data={"name": auto})
    assert get_schedule(auto).teammate_id == made["id"]
    assert client.delete(f"/spa/teammates/{made['id']}", headers=SAME).status_code == 200
    kept = get_schedule(auto)
    assert kept is not None and kept.teammate_id is None


def test_a_scheduled_run_is_told_the_brief_and_what_the_user_said():
    t = create_teammate(name=_name(), agent="orchestrator", brief="Watch gilt yields.")
    assert run_context(None) is None and run_context("nope") is None
    first = run_context(t.id)
    assert "Watch gilt yields." in first and "What the user has told you" not in first

    sid = str(uuid.uuid4())
    from bran.persistence import set_teammate_chat

    set_teammate_chat(t.id, sid)
    _run(source="chat", session_id=sid, task="Lead with the 10-year from now on.",
         started_at="2026-11-01T09:00:00+00:00")
    _run(source="chat", session_id=sid, task="And drop the equities section.",
         started_at="2026-11-02T09:00:00+00:00")
    _run(source="spawn", session_id=sid, task="a background run, not the user")
    told = run_context(t.id)
    assert told.index("Lead with the 10-year") < told.index("drop the equities section")
    assert "a background run" not in told


def test_the_chat_is_told_its_automations_and_latest_deliveries():
    t = create_teammate(name=_name(), agent="orchestrator", brief="Brief me on FX.",
                        schedule="every day at 6am")
    from bran.teammates import automations_of

    auto = automations_of(t.id)[0]
    _run(schedule_id=auto.id, result="EURUSD fell through 1.05.")
    _run(schedule_id=auto.id, result="", status="failed")
    told = chat_context(get_teammate(t.id))
    assert "Brief me on FX." in told and auto.name in told and "06:00" in told
    assert "EURUSD fell through 1.05." in told


def _fake_stream(monkeypatch, sid: str) -> dict:
    seen: dict = {}

    async def fake(agent, prompt, *, resume_session=None, session_id=None, append_system=None, **kw):
        seen.update(agent=agent, prompt=prompt, resume=resume_session, append_system=append_system)
        yield SimpleNamespace(parent_tool_use_id=None)

    monkeypatch.setattr(spa, "stream_agent", fake)
    monkeypatch.setattr(spa, "events_from_message", lambda _m: [{"type": "session", "session_id": sid}])
    monkeypatch.setattr(spa, "find_session_file", lambda _sid: Path("/tmp/present.jsonl"))
    return seen


def test_a_teammate_has_one_chat_and_it_stays_out_of_recents(client, monkeypatch):
    name = _name()
    made = client.post("/spa/teammates", headers=SAME,
                       data={"name": name, "agent": "finance-news", "brief": "Own the morning brief."}).json()
    sid = str(uuid.uuid4())
    seen = _fake_stream(monkeypatch, sid)

    # First message: no session yet. The reply comes from its role, with its brief.
    r = client.post("/spa/chat/stream", headers=SAME,
                    data={"prompt": "@research hello", "teammate_id": made["id"]})
    assert r.status_code == 200
    assert seen["agent"] == "finance-news" and seen["resume"] is None
    assert seen["prompt"] == "@research hello"  # an @mention is not a reroute here
    assert "Own the morning brief." in seen["append_system"] and name in seen["append_system"]
    assert get_teammate(made["id"]).chat_id == sid
    assert client.get(f"/spa/chats/{sid}", headers=SAME).json()["teammate"] == {"id": made["id"], "name": name}
    assert client.get(f"/spa/chats/{sid}", headers=SAME).json()["title"] == name

    # Later, even addressed by teammate alone, it resumes that same chat.
    client.post("/spa/chat/stream", headers=SAME, data={"prompt": "again", "teammate_id": made["id"]})
    assert seen["resume"] == sid
    # And by session alone it is still recognised.
    seen.clear()
    client.post("/spa/chat/stream", headers=SAME, data={"prompt": "third", "session_id": sid})
    assert seen["agent"] == "finance-news" and "Own the morning brief." in seen["append_system"]

    assert sid not in [c["id"] for c in client.get("/spa/chats", headers=SAME).json()]
    assert sid in [c.id for c in list_chats(limit=1000)]

    # Deleting the chat frees the teammate to start a new one.
    assert client.delete(f"/spa/chats/{sid}", headers=SAME).status_code == 200
    assert get_teammate(made["id"]).chat_id is None


def test_an_ordinary_chat_is_untouched(client, monkeypatch):
    sid = str(uuid.uuid4())
    upsert_chat(ChatRecord(id=sid, title="plain", agent="orchestrator"))
    seen = _fake_stream(monkeypatch, sid)
    client.post("/spa/chat/stream", headers=SAME, data={"prompt": "hi", "session_id": sid})
    assert seen["agent"] == "orchestrator" and not (seen["append_system"] or "")
    assert client.get(f"/spa/chats/{sid}", headers=SAME).json()["teammate"] is None


def test_an_existing_database_gains_the_teammate_column():
    import sqlite3

    from bran.persistence import _MIGRATIONS, _m4_teammates

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE schedules (id TEXT PRIMARY KEY, name TEXT)")
    _m4_teammates(conn)
    _m4_teammates(conn)  # harmless twice
    assert "teammate_id" in [c["name"] for c in conn.execute("PRAGMA table_info(schedules)")]
    assert _m4_teammates in _MIGRATIONS


# --- deliveries in the chat, and asking before acting ---------------------------

def _mate_with_auto(schedule: str = "every day at 6am"):
    from bran.teammates import automations_of

    t = create_teammate(name=_name(), agent="orchestrator", brief="Brief me on FX.", schedule=schedule)
    return t, automations_of(t.id)[0]


def test_deliveries_land_in_the_chat_between_turns(client):
    from bran.persistence import update_run
    from bran.teammates import feed, merge_feed

    t, auto = _mate_with_auto()
    sent_back = _run(schedule_id=auto.id, result="# Draft\nWrong.")
    sent_back.metadata["verification"] = {"pass": False, "retried_run_id": "x"}
    update_run(sent_back)
    good = _run(schedule_id=auto.id, result="# FX brief\nEURUSD fell through 1.05.")

    events = feed(get_teammate(t.id))
    # The report the reviewer sent back is not a delivery; its replacement is.
    assert [e["run_id"] for e in events if e["type"] == "delivery"] == [good.id]
    assert events[0]["title"] == "FX brief" and "EURUSD" in events[0]["excerpt"]
    assert events[0]["schedule"] == auto.name

    # Placed by time, and never inside a turn.
    talk = [
        {"type": "user", "text": "before", "ts": "2000-01-01T00:00:00"},
        {"type": "text", "text": "ok", "ts": "2000-01-01T00:00:05"},
        {"type": "user", "text": "after", "ts": "2999-01-01T00:00:00"},
        {"type": "text", "text": "ok", "ts": "2999-01-01T00:00:05"},
    ]
    merged = merge_feed(talk, events)
    assert [e["type"] for e in merged] == ["user", "text", "delivery", "user", "text"]
    assert merge_feed([], events) == events

    assert client.get(f"/spa/teammates/{t.id}/feed", headers=SAME).json()["events"] == events


def test_a_teammate_asks_and_a_yes_makes_the_change(client):
    from bran.teammates import ask, automations_of

    t, auto = _mate_with_auto()
    run = _run(schedule_id=auto.id, result="report")
    q = ask(t.id, title="Add a Friday wrap-up?", detail="A weekly view would help.",
            kind="new_automation", schedule="every Friday at 4pm", task="Write the weekly wrap-up.",
            run_id=run.id)
    assert q.status == "pending" and q.action["cron"] == "0 16 * * fri"
    assert len(automations_of(t.id)) == 1  # nothing changes on asking

    # It is told what is waiting, in a run and in the chat; the chat shows it.
    assert "Add a Friday wrap-up?" in run_context(t.id)
    assert "mcp__bran_team__ask_approval" in chat_context(get_teammate(t.id))
    listed = client.get(f"/spa/teammates/{t.id}", headers=SAME).json()
    assert listed["waiting"] == 1 and listed["requests"][0]["id"] == q.id
    assert {"type": "request", "request_id": q.id, "ts": q.created_at} in client.get(
        f"/spa/teammates/{t.id}/feed", headers=SAME).json()["events"]

    # The same question again, or a fourth open one, is refused.
    with pytest.raises(TeammateError):
        ask(t.id, title="add a friday wrap-up?")
    ask(t.id, title="Second?")
    ask(t.id, title="Third?")
    with pytest.raises(TeammateError):
        ask(t.id, title="Fourth?")

    done = client.post(f"/spa/teammates/{t.id}/requests/{q.id}", headers=SAME,
                       data={"decision": "approve", "note": "Keep it short."}).json()
    assert done["status"] == "approved" and "added the automation" in done["outcome"]
    autos = automations_of(t.id)
    assert len(autos) == 2
    new = next(s for s in autos if s.id != auto.id)
    assert new.cron == "0 16 * * fri" and new.task == "Write the weekly wrap-up." and new.agent == "orchestrator"
    told = run_context(t.id)
    assert "approved" in told and "Keep it short." in told

    # Answered once: a second answer is a conflict, and another teammate's id is not found.
    again = client.post(f"/spa/teammates/{t.id}/requests/{q.id}", headers=SAME, data={"decision": "decline"})
    assert again.status_code == 409
    other, _ = _mate_with_auto()
    assert client.post(f"/spa/teammates/{other.id}/requests/{q.id}", headers=SAME,
                       data={"decision": "approve"}).status_code == 404


def test_declining_changes_nothing_and_other_kinds_do_what_they_say(client):
    from bran.teammates import ask, decide

    t, auto = _mate_with_auto()
    no = ask(t.id, title="Rewrite my brief?", kind="change_brief", brief="Cover rates only.")
    assert decide(no.id, approve=False, note="No, keep FX.").status == "declined"
    assert get_teammate(t.id).brief == "Brief me on FX."
    assert "declined" in run_context(t.id) and "No, keep FX." in run_context(t.id)

    yes = ask(t.id, title="Rewrite my brief, with FX?", kind="change_brief", brief="Cover FX and rates.")
    assert decide(yes.id, approve=True).outcome == "replaced the standing brief"
    assert get_teammate(t.id).brief == "Cover FX and rates."

    move = ask(t.id, title="Run earlier?", kind="change_schedule", schedule="every day at 5am")
    assert move.action["automation"] == auto.name
    decide(move.id, approve=True)
    assert get_schedule(auto.name).cron == "0 5 * * *"

    plain = ask(t.id, title="Lead with the dollar from now on?")
    assert decide(plain.id, approve=True).outcome == ""

    # What a yes would need is checked when asking, not when answering.
    for bad in (
        dict(kind="new_automation", schedule="whenever", task="x"),
        dict(kind="new_automation", schedule="every day at 6am"),
        dict(kind="change_brief"),
        dict(kind="change_schedule", schedule="every day at 6am", automation="not-mine"),
        dict(kind="delete_everything"),
    ):
        with pytest.raises(TeammateError):
            ask(t.id, title="Bad?", **bad)


def test_the_teammate_tools_know_whose_run_they_are_in():
    import asyncio

    from bran.background import current_run_id
    from bran.persistence import list_teammate_requests
    from bran.tools.team import ask_approval, read_delivery

    t, auto = _mate_with_auto()
    report = _run(schedule_id=auto.id, result="# FX brief\nThe whole report.")
    stranger = _run(result="not this teammate")

    async def call(tool, args, run_id):
        token = current_run_id.set(run_id)
        try:
            return await tool.handler(args)
        finally:
            current_run_id.reset(token)

    out = asyncio.run(call(ask_approval, {"title": "Lead with the dollar?"}, report.id))
    assert "request_id=" in out["content"][0]["text"]
    assert [q.title for q in list_teammate_requests(t.id)] == ["Lead with the dollar?"]

    text = asyncio.run(call(read_delivery, {"run_id": report.id}, report.id))["content"][0]["text"]
    assert "The whole report." in text
    # Not its own run: refused. Outside a teammate run: refused.
    assert asyncio.run(call(read_delivery, {"run_id": stranger.id}, report.id)).get("is_error")
    assert asyncio.run(call(ask_approval, {"title": "x"}, stranger.id)).get("is_error")


def test_a_teammate_run_gets_its_tools_and_others_do_not():
    from bran.agents import build_options_for, get_agent
    from bran.teammates import TEAM_TOOLS

    agent = get_agent("finance-news")
    plain = build_options_for(agent)
    assert "bran_team" not in plain.mcp_servers
    assert not set(TEAM_TOOLS) & set(plain.allowed_tools)
    mine = build_options_for(agent, extra_tools=list(TEAM_TOOLS))
    assert "bran_team" in mine.mcp_servers and set(TEAM_TOOLS) <= set(mine.allowed_tools)
    assert set(plain.allowed_tools) <= set(mine.allowed_tools)
    assert plain.disallowed_tools == mine.disallowed_tools


def test_a_delivery_excerpt_is_the_report_not_the_covering_note():
    from bran.teammates import delivery_title

    run = RunRecord.new(agent="research", task="t", source="runner")
    run.result = (
        "Fact-check done: 10 of 12 claims held. Saved at `/home/x/briefings/b.md`.\n\n"
        "# Finance Briefing: Wednesday\n**Lens:** FX volatility\n\n"
        "> **Delta note:** only what changed.\n\n## What changed\n"
        "- **NEW: Nvidia reported.** It beat on sales.\n- Sterling slipped.\n"
    )
    title, text = delivery_title(run, "morning-brief")
    assert title == "Finance Briefing: Wednesday"
    assert text.startswith("NEW: Nvidia reported. It beat on sales. Sterling slipped.")
    assert "Fact-check" not in text and "Lens" not in text and "Delta note" not in text

    run.result = "Rates were steady.\nNothing else moved."
    assert delivery_title(run, "morning-brief") == ("morning-brief", "Rates were steady. Nothing else moved.")
