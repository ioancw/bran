"""Sources: cited links and domains in an answer, pages a run (or the subagents
it delegated to) fetched, de-duplicated and ordered — plus the chat rename /
move-to-loose endpoints that ship alongside the sidebar menu."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from bran.api import build_app
from bran.persistence import ChatRecord, RunRecord, get_chat, insert_run, upsert_chat
from bran.sources import domain_of, extract_sources, sources_for_run


def test_extract_orders_cited_before_fetched_and_dedupes():
    result = (
        "The rate is 3.75% (Source: bankofengland.co.uk).\n\n"
        "Sources:\n- [Bank Rate](https://www.bankofengland.co.uk/monetary-policy/the-interest-rate-bank-rate)\n"
        "- https://www.ecb.europa.eu/stats/rates.html.\n"
    )
    events = [
        {"type": "tool_use", "name": "mcp__tavily__tavily_extract",
         "input": {"urls": ["https://www.bankofengland.co.uk/monetary-policy/the-interest-rate-bank-rate/"]}},
        {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://feeds.bbci.co.uk/news/business/rss.xml"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
        {"type": "tool_use", "name": "mcp__tavily__tavily_search", "input": {"query": "rates"}},
    ]
    out = extract_sources(result, events)
    assert [s["domain"] for s in out] == ["bankofengland.co.uk", "ecb.europa.eu", "feeds.bbci.co.uk"]
    assert out[0]["title"] == "Bank Rate" and out[0]["via"] == "cited"
    assert out[1]["url"] == "https://www.ecb.europa.eu/stats/rates.html"  # trailing period stripped
    assert out[2]["via"] == "fetched" and out[2]["title"] is None


def test_cited_domain_without_url_is_listed_once():
    out = extract_sources("Fact A (Source: reuters.com). Fact B (Sources: reuters.com, ft.com)", [])
    assert [(s["domain"], s["url"]) for s in out] == [("reuters.com", None), ("ft.com", None)]
    assert domain_of("https://www.FT.com/x") == "ft.com" and domain_of("nonsense") == ""


def test_sources_for_run_includes_delegated_children():
    parent = RunRecord.new(agent="orchestrator", task="t", source="chat")
    parent.status = "completed"
    parent.result = "Answer (Source: jpl.nasa.gov)."
    parent.session_id = "sess-" + uuid.uuid4().hex
    insert_run(parent)
    child = RunRecord.new(agent="research", task="t", source="subagent", parent_run_id=parent.id)
    child.status = "completed"
    child.metadata["events"] = [
        {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://www.jpl.nasa.gov/mars"}},
    ]
    insert_run(child)
    out = sources_for_run(parent)
    assert [s["domain"] for s in out] == ["jpl.nasa.gov"] and out[0]["url"] == "https://www.jpl.nasa.gov/mars"
    with TestClient(build_app(enable_scheduler=False)) as c:
        assert c.get(f"/spa/runs/{parent.id}/sources").json()[0]["domain"] == "jpl.nasa.gov"
        rows = c.get(f"/spa/chats/{parent.session_id}/sources").json()
        assert rows[0]["run_id"] == parent.id and rows[0]["domain"] == "jpl.nasa.gov"


def test_rename_and_move_to_loose():
    sid = "sess-" + uuid.uuid4().hex
    upsert_chat(ChatRecord(id=sid, title="old", agent="orchestrator"))
    with TestClient(build_app(enable_scheduler=False)) as c:
        assert c.post(f"/spa/chats/{sid}/rename", data={"title": "  New   title  "}).json()["title"] == "New title"
        assert c.post(f"/spa/chats/{sid}/rename", data={"title": "   "}).status_code == 400
        assert c.post("/spa/chats/nope/rename", data={"title": "x"}).status_code == 404
        assert c.post(f"/spa/chats/{sid}/move", data={"project_id": ""}).json()["project_id"] is None
        assert c.post(f"/spa/chats/{sid}/move", data={"project_id": "ghost"}).status_code == 404
    assert get_chat(sid).title == "New title" and get_chat(sid).project_id is None


def test_model_override_reaches_the_sdk_options():
    from bran.agents import build_options_for, get_agent

    assert build_options_for(get_agent("summariser")).model == "haiku"
    assert build_options_for(get_agent("summariser"), model="opus").model == "opus"
