"""Around an answer: site icons for sources, suggested follow-ups, thumbs
up/down, documents saved outside a project, and the notification's link."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from bran import favicons, followups
from bran.api import build_app
from bran.persistence import ChatRecord, RunRecord, all_answer_feedback, delete_chat, upsert_chat

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SAME = {"sec-fetch-site": "same-origin"}


@pytest.fixture(scope="module")
def client():
    with TestClient(build_app()) as c:
        yield c


# --- favicons ----------------------------------------------------------------

def test_image_type_accepts_rasters_and_rejects_markup():
    assert favicons.image_type(PNG) == "image/png"
    assert favicons.image_type(b"\x00\x00\x01\x00rest") == "image/x-icon"
    assert favicons.image_type(b"GIF89a...") == "image/gif"
    assert favicons.image_type(b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>") is None
    assert favicons.image_type(b"<!doctype html><html>404</html>") is None


def test_candidates_walk_up_but_stop_at_a_registry():
    assert favicons.candidates("feeds.bbci.co.uk") == ["feeds.bbci.co.uk", "bbci.co.uk"]
    assert favicons.candidates("bbc.co.uk") == ["bbc.co.uk"]
    assert favicons.candidates("www.ft.com") == ["www.ft.com", "ft.com"]
    assert favicons.candidates("ft.com") == ["ft.com"]


def test_valid_domain():
    assert favicons.valid_domain("bbc.co.uk")
    for bad in ("localhost", "127.0.0.1", "a b.com", "../etc/passwd", "-x.com", ""):
        assert not favicons.valid_domain(bad), bad


def test_favicon_is_fetched_once_then_served_from_disk(client, monkeypatch):
    calls: list[str] = []

    async def fake_download(url: str):
        calls.append(url)
        return PNG

    monkeypatch.setattr(favicons, "_download", fake_download)
    r = client.get("/spa/favicon/icons-one.example.com", headers=SAME)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == PNG
    assert client.get("/spa/favicon/icons-one.example.com", headers=SAME).status_code == 200
    assert calls == ["https://icons-one.example.com/favicon.ico"]


def test_a_site_without_a_usable_icon_is_a_cached_404(client, monkeypatch):
    calls: list[str] = []

    async def fake_download(url: str):
        calls.append(url)
        return b"<html>not an image</html>"

    monkeypatch.setattr(favicons, "_download", fake_download)
    assert client.get("/spa/favicon/noicon.example.org", headers=SAME).status_code == 404
    assert client.get("/spa/favicon/noicon.example.org", headers=SAME).status_code == 404
    # One look (the host, then its parent site); the miss is then remembered.
    assert calls == ["https://noicon.example.org/favicon.ico", "https://example.org/favicon.ico"]


def test_favicon_rejects_non_domains_and_can_be_switched_off(client, monkeypatch):
    assert client.get("/spa/favicon/127.0.0.1", headers=SAME).status_code == 400

    async def boom(url: str):
        raise AssertionError("no request may be made when icons are off")

    monkeypatch.setattr(favicons, "_download", boom)
    monkeypatch.setenv("BRAN_FAVICONS", "0")
    assert client.get("/spa/favicon/off.example.net", headers=SAME).status_code == 404


def test_favicon_download_refuses_private_addresses():
    # The egress guard applies: a name resolving to loopback is never fetched.
    assert asyncio.run(favicons._download("https://localhost/favicon.ico")) is None


# --- follow-ups ---------------------------------------------------------------

def test_parse_suggestions_takes_a_json_array_and_nothing_else():
    raw = 'Sure!\n["What moved the pound?", "  How did gilts   react? ", "What moved the pound?", 7, ""]'
    assert followups.parse_suggestions(raw) == ["What moved the pound?", "How did gilts react?"]
    assert followups.parse_suggestions("no json here") == []
    assert followups.parse_suggestions("[not, valid]") == []
    assert len(followups.parse_suggestions('["a b c", "d e f", "g h i", "j k l"]')) == 3


def test_followups_endpoint_returns_the_models_suggestions(client, monkeypatch):
    seen: list[str] = []

    async def fake_ask(prompt: str) -> str:
        seen.append(prompt)
        return '["Why did oil fall?", "What about gas?"]'

    monkeypatch.setattr(followups, "_ask", fake_ask)
    r = client.post("/spa/followups", data={"prompt": "markets today?", "answer": "Oil fell 2%."}, headers=SAME)
    assert r.status_code == 200
    assert r.json() == {"suggestions": ["Why did oil fall?", "What about gas?"]}
    assert "markets today?" in seen[0] and "Oil fell 2%." in seen[0]


def test_followups_are_empty_when_off_or_when_the_model_fails(client, monkeypatch):
    async def broken(prompt: str) -> str:
        raise RuntimeError("no login")

    monkeypatch.setattr(followups, "_ask", broken)
    r = client.post("/spa/followups", data={"prompt": "q", "answer": "a"}, headers=SAME)
    assert r.status_code == 200 and r.json() == {"suggestions": []}

    async def never(prompt: str) -> str:
        raise AssertionError("no model call when switched off")

    monkeypatch.setattr(followups, "_ask", never)
    monkeypatch.setenv("BRAN_FOLLOWUPS", "0")
    r = client.post("/spa/followups", data={"prompt": "q", "answer": "a"}, headers=SAME)
    assert r.json() == {"suggestions": []}


# --- feedback -----------------------------------------------------------------

def test_rating_an_answer_is_stored_changed_cleared_and_dies_with_the_chat(client):
    sid = "fb-chat-1"
    upsert_chat(ChatRecord(id=sid, title="t", agent="orchestrator", project_id=None))
    url = f"/spa/chats/{sid}/feedback"
    body = {"key": "k1", "rating": "1", "prompt": "what moved?", "answer": "Oil.", "agent": "orchestrator"}
    assert client.post(url, data=body, headers=SAME).status_code == 200
    assert client.get(url, headers=SAME).json() == {"k1": 1}
    stored = [r for r in all_answer_feedback() if r["session_id"] == sid]
    assert stored[0]["prompt"] == "what moved?" and stored[0]["answer"] == "Oil." and stored[0]["rating"] == 1

    assert client.post(url, data={**body, "rating": "-1"}, headers=SAME).status_code == 200
    assert client.get(url, headers=SAME).json() == {"k1": -1}
    assert client.post(url, data={"key": "k2", "rating": "1"}, headers=SAME).status_code == 200
    assert client.post(url, data={"key": "k2", "rating": "0"}, headers=SAME).status_code == 200
    assert client.get(url, headers=SAME).json() == {"k1": -1}

    assert client.post(url, data={"key": "k3", "rating": "5"}, headers=SAME).status_code == 400
    assert client.post(url, data={"key": " ", "rating": "1"}, headers=SAME).status_code == 400
    assert client.post(url, data=body, headers={"sec-fetch-site": "cross-site"}).status_code == 403

    assert delete_chat(sid)
    assert client.get(url, headers=SAME).json() == {}


# --- documents saved outside a project ---------------------------------------

def test_loose_documents_are_served_sandboxed_and_only_from_their_folder(client):
    from bran.doc_export import documents_dir

    (documents_dir() / "note.html").write_text("<h1>hi</h1>", encoding="utf-8")
    r = client.get("/spa/documents/note.html", headers=SAME)
    assert r.status_code == 200 and "<h1>hi</h1>" in r.text
    assert r.headers["content-security-policy"] == "sandbox allow-scripts allow-modals"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "inline" in r.headers["content-disposition"]
    again = client.get("/spa/documents/note.html?download=1", headers=SAME)
    assert "attachment" in again.headers["content-disposition"]
    assert client.get("/spa/documents/missing.html", headers=SAME).status_code == 404
    # A name with path parts never reaches the handler (the route takes one
    # component); whatever answers, it is not a file from outside the folder.
    escaped = client.get("/spa/documents/..%2F..%2Fbran.sqlite", headers=SAME)
    assert not escaped.content.startswith(b"SQLite format")
    assert "content-disposition" not in escaped.headers


# --- notification link ---------------------------------------------------------

def test_notifications_link_to_the_reading_view_only_when_a_public_url_is_set(monkeypatch):
    from bran.notify import read_link, webhook_payload

    rec = RunRecord(id="run-123", agent="finance-news", task="brief", status="completed", result="ok")
    monkeypatch.delenv("BRAN_PUBLIC_URL", raising=False)
    assert read_link(rec) is None
    assert "url" not in webhook_payload(rec, False)
    monkeypatch.setenv("BRAN_PUBLIC_URL", "https://box.tailnet.ts.net:8765/")
    assert read_link(rec) == "https://box.tailnet.ts.net:8765/app/read/run-123"
    assert webhook_payload(rec, False)["url"] == "https://box.tailnet.ts.net:8765/app/read/run-123"
    monkeypatch.setenv("BRAN_PUBLIC_URL", "javascript:alert(1)")
    assert read_link(rec) is None


# --- the library (Artifacts page) ------------------------------------------------

def test_library_lists_project_files_and_loose_documents_newest_first(client):
    import os

    from bran.doc_export import documents_dir
    from bran.persistence import ProjectRecord, insert_project
    from bran.project_files import files_dir

    proj = ProjectRecord.new(name="Library project")
    insert_project(proj)
    page = files_dir(proj.id, create=True) / "fx vol.html"
    page.write_text("<html><head><title>FX vol &amp; skew</title></head></html>", encoding="utf-8")
    loose = documents_dir() / "loose-note.md"
    loose.write_text("# hi", encoding="utf-8")
    os.utime(page, (2_000_000_000, 2_000_000_000))
    os.utime(loose, (2_000_000_100, 2_000_000_100))

    rows = client.get("/spa/library", headers=SAME).json()
    mine = [r for r in rows if r["name"] in ("fx vol.html", "loose-note.md")]
    assert [r["name"] for r in mine] == ["loose-note.md", "fx vol.html"]
    assert mine[0]["project_id"] is None and mine[0]["title"] == ""
    assert mine[0]["url"] == "/spa/documents/loose-note.md"
    assert mine[1]["project_name"] == "Library project" and mine[1]["title"] == "FX vol & skew"
    assert mine[1]["url"] == f"/spa/projects/{proj.id}/files/fx%20vol.html"
    # The URL each entry gives is one the server actually serves.
    assert client.get(mine[1]["url"], headers=SAME).status_code == 200
    assert client.get(mine[0]["url"], headers=SAME).status_code == 200
