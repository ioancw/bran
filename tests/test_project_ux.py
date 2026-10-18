"""Project summary, chat archive, and the one-sentence runner draft."""

from __future__ import annotations

import asyncio
import json

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from fastapi.testclient import TestClient

from bran.api import build_app
from bran.persistence import (
    ChatRecord,
    ProjectRecord,
    RunRecord,
    count_chats_per_project,
    get_project,
    insert_project,
    insert_run,
    list_chats,
    list_schedules,
    set_chat_archived,
    set_project_summary,
    upsert_chat,
)


@pytest.fixture
def client():
    with TestClient(build_app(enable_scheduler=False)) as c:
        yield c


def _project(name="Alpha") -> ProjectRecord:
    p = ProjectRecord.new(name=name, description="d")
    insert_project(p)
    return p


# --- archive ---------------------------------------------------------------

def test_archived_chats_leave_recents_but_are_kept(client):
    p = _project()
    for i in range(3):
        upsert_chat(ChatRecord(id=f"c{i}", title=f"chat {i}", agent="orchestrator", project_id=p.id))
    assert set_chat_archived("c1", True)
    assert [c.id for c in list_chats(project_id=p.id)] == ["c2", "c0"]
    assert [c.id for c in list_chats(project_id=p.id, archived=True)] == ["c1"]
    assert len(list_chats(project_id=p.id, archived=None)) == 3
    assert count_chats_per_project()[p.id] == 2

    r = client.get(f"/spa/chats?project_id={p.id}&archived=true").json()
    assert [c["id"] for c in r] == ["c1"] and r[0]["archived_at"]
    d = client.get(f"/spa/projects/{p.id}").json()
    assert len(d["chats"]) == 2 and d["n_archived"] == 1

    assert client.post("/spa/chats/c1/archive", data={"archived": "false"}).json()["archived_at"] is None
    assert len(list_chats(project_id=p.id)) == 3
    assert client.post("/spa/chats/nope/archive", data={"archived": "true"}).status_code == 404


# --- summary ---------------------------------------------------------------

def test_summary_is_stored_and_rides_in_the_project_context(client):
    from bran.web.spa_api import _build_project_context

    p = _project("Beta")
    assert set_project_summary(p.id, "# Beta\nIt is about bees.")
    got = get_project(p.id)
    assert got.summary.startswith("# Beta") and got.summary_at
    ctx = _build_project_context(got)
    assert "## Project summary (auto-written" in ctx and "about bees" in ctx
    d = client.get(f"/spa/projects/{p.id}").json()
    assert d["project"]["summary"].startswith("# Beta") and d["daily_summary"] is None


def test_activity_digest_and_the_tools(client):
    from bran.tools.projects import (
        get_project_activity,
        project_activity_digest,
        save_project_summary,
    )

    p = _project("Gamma")
    upsert_chat(ChatRecord(id="s1", title="rates question", agent="orchestrator", project_id=p.id))
    turn = RunRecord.new(agent="orchestrator", task="rates?", project_id=p.id, source="chat")
    turn.session_id = "s1"
    turn.status, turn.result = "completed", "The BoE held at 3.75%."
    insert_run(turn)
    bg = RunRecord.new(agent="research", task="dig into gilts", project_id=p.id, source="spawn")
    bg.status, bg.result = "completed", "Gilt yields fell."
    insert_run(bg)
    digest = project_activity_digest(p.id)
    assert "# Project: Gamma" in digest and "rates question" in digest
    assert "Last reply: The BoE held" in digest and "dig into gilts" in digest and "Gilt yields fell" in digest
    assert project_activity_digest("nope") is None

    out = asyncio.run(get_project_activity.handler({"project_id": p.id}))
    assert "Gamma" in out["content"][0]["text"]
    out = asyncio.run(save_project_summary.handler({"project_id": p.id, "text": "Gamma is about gilts."}))
    assert "Saved" in out["content"][0]["text"]
    assert get_project(p.id).summary == "Gamma is about gilts."
    out = asyncio.run(save_project_summary.handler({"project_id": p.id, "text": "  "}))
    assert "Error" in out["content"][0]["text"]


def test_daily_summary_runner_toggle(client):
    from bran.project_summary import find_summary_schedule, is_summary_schedule

    p = _project("Delta Force")
    r = client.post(f"/spa/projects/{p.id}/daily-summary", data={"enabled": "true"}).json()
    assert r["daily_summary"] == "delta-force-summary"
    s = find_summary_schedule(p.id)
    assert s is not None and s.agent == "summariser" and s.cron == "0 18 * * *" and is_summary_schedule(s)
    assert p.id in s.task
    # idempotent
    assert client.post(f"/spa/projects/{p.id}/daily-summary", data={"enabled": "true"}).json()["daily_summary"] == "delta-force-summary"
    assert client.get(f"/spa/projects/{p.id}").json()["daily_summary"] == "delta-force-summary"
    r = client.post(f"/spa/projects/{p.id}/daily-summary", data={"enabled": "false"}).json()
    assert r["daily_summary"] is None and find_summary_schedule(p.id) is None
    assert not any(is_summary_schedule(s) for s in list_schedules())


def test_new_project_can_start_with_a_daily_summary(client):
    r = client.post("/spa/projects", data={"name": "Echo", "description": "", "daily_summary": "true"}).json()
    from bran.project_summary import find_summary_schedule

    assert find_summary_schedule(r["id"]) is not None


def test_summarise_now_launches_a_summariser_run(client, monkeypatch):
    import bran.web.spa_api as spa

    launched = {}

    async def fake_run_agent(agent, task, *, record=None, **kw):
        launched["agent"], launched["task"], launched["project"] = agent, task, record.project_id

    monkeypatch.setattr(spa, "run_agent", fake_run_agent)
    p = _project("Zeta")
    r = client.post(f"/spa/projects/{p.id}/summarise")
    assert r.status_code == 200 and r.json()["agent"] == "summariser"
    assert launched["agent"] == "summariser" and p.id in launched["task"] and launched["project"] == p.id
    assert client.post("/spa/projects/nope/summarise").status_code == 404


# --- runner draft ----------------------------------------------------------

def _fake_query(payload: dict):
    async def q(prompt, options):
        yield AssistantMessage(content=[TextBlock("Here you go:\n" + json.dumps(payload))], model="m")

    return q


def test_runner_draft_fills_the_form(client, monkeypatch):
    import bran.runner_draft as rd

    p = _project("FX desk")
    monkeypatch.setattr(rd, "query", _fake_query({
        "name": "Morning FX Brief!", "agent": "finance-news", "kind": "cron",
        "schedule": "every weekday at 7am", "task": "Brief me on overnight FX moves.",
        "project": "fx desk", "verify": True, "delta": False, "alert": "",
    }))
    r = client.post("/spa/schedules/draft", data={"text": "every weekday at 7 brief me on fx"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "morning-fx-brief" and d["agent"] == "finance-news"
    assert d["cron"] == "0 7 * * mon-fri" and d["human"] and not d["schedule_error"]
    assert d["project_id"] == p.id and d["verify"] is True and d["task"].startswith("Brief me")


def test_runner_draft_survives_a_bad_schedule_and_unknown_agent(client, monkeypatch):
    import bran.runner_draft as rd

    monkeypatch.setattr(rd, "query", _fake_query({
        "name": "x", "agent": "nobody", "kind": "cron", "schedule": "whenever mercury is in retrograde",
        "task": "do it", "project": "", "verify": False, "delta": True, "alert": "big moves",
    }))
    d = client.post("/spa/schedules/draft", data={"text": "do it when mercury is in retrograde"}).json()
    assert d["agent"] == "orchestrator" and d["cron"] == "" and d["schedule_error"]
    assert d["schedule"] == "whenever mercury is in retrograde" and d["delta"] is True and d["alert"] == "big moves"


def test_runner_draft_once(client, monkeypatch):
    import bran.runner_draft as rd

    monkeypatch.setattr(rd, "query", _fake_query({
        "name": "one-off", "agent": "research", "kind": "once", "schedule": "",
        "run_at": "2026-12-01T15:00", "task": "Check the thing.", "project": "",
    }))
    d = client.post("/spa/schedules/draft", data={"text": "on 1 December at 3pm check the thing"}).json()
    assert d["kind"] == "once" and d["run_at"] == "2026-12-01T15:00" and d["cron"] == ""


def test_runner_draft_rejects_a_grunt(client):
    assert client.post("/spa/schedules/draft", data={"text": "hi"}).status_code == 400
