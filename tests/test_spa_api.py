"""Unit tests for SPA API helpers + the /spa same-origin (CSRF) guard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from bran.api import build_app
from bran.persistence import ScheduleRecord
from bran.web.spa_api import _schedule_dict


@pytest.fixture(scope="module")
def client():
    with TestClient(build_app(enable_scheduler=False)) as c:
        yield c


# ---------------------------------------------------------------------------
# Same-origin guard. /spa has no bearer token, so this dependency is the only
# thing stopping arbitrary websites from firing form POSTs at localhost.
# ---------------------------------------------------------------------------

def test_spa_allows_non_browser_clients(client):
    # No Origin / Sec-Fetch-Site headers (curl, httpx): CSRF doesn't apply.
    assert client.get("/spa/schedules").status_code == 200


def test_spa_allows_same_origin_fetch(client):
    r = client.get("/spa/schedules", headers={"sec-fetch-site": "same-origin"})
    assert r.status_code == 200


def test_spa_allows_direct_navigation(client):
    r = client.get("/spa/schedules", headers={"sec-fetch-site": "none"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Output reading state: durable star + read markers, and free-text search.
# ---------------------------------------------------------------------------

def test_output_star_read_and_search(client):
    from bran.persistence import RunRecord, insert_run

    rec = RunRecord.new(agent="research", task="weekly fintech digest", source="runner")
    rec.status = "completed"
    rec.result = "Acme raised a Series B; Globex launched a new card."
    insert_run(rec)

    def row():
        return next(r for r in client.get("/spa/runs").json() if r["id"] == rec.id)

    # A fresh output is unstarred + unread.
    assert row()["starred"] is False
    assert row()["read_at"] is None

    # Star, then mark read — both persist and survive a re-fetch.
    r = client.post(f"/spa/outputs/{rec.id}/star", data={"starred": "true"})
    assert r.status_code == 200 and r.json()["starred"] is True
    assert row()["starred"] is True

    r = client.post("/spa/outputs/read", data={"ids": rec.id})
    assert r.status_code == 200 and r.json()["read"] == 1
    assert row()["read_at"] is not None
    assert row()["starred"] is True  # marking read preserves the star

    # Search matches the result text; a non-match excludes it.
    hit = client.get("/spa/runs", params={"q": "Globex"}).json()
    assert any(r["id"] == rec.id for r in hit)
    miss = client.get("/spa/runs", params={"q": "zzz-no-such-term-zzz"}).json()
    assert all(r["id"] != rec.id for r in miss)

    # Unstarring restores the unstarred state (read_at untouched).
    client.post(f"/spa/outputs/{rec.id}/star", data={"starred": "false"})
    assert row()["starred"] is False
    assert row()["read_at"] is not None


def test_star_unknown_run_404s(client):
    assert client.post("/spa/outputs/nope/star", data={"starred": "true"}).status_code == 404


def test_runs_scoped_to_session(client):
    """The chat-side Progress rail asks for runs by session_id — it must return
    this chat's spawned background runs and exclude other conversations'."""
    from bran.persistence import RunRecord, insert_run

    chat = RunRecord.new(agent="orchestrator", task="t", source="chat")
    chat.session_id = "sess-scope-test"
    chat.status = "completed"
    insert_run(chat)

    child = RunRecord.new(agent="research", task="dig", source="spawn", parent_run_id=chat.id)
    child.status = "completed"
    child.result = "found things"
    insert_run(child)

    other = RunRecord.new(agent="research", task="unrelated", source="spawn")
    other.status = "completed"
    other.result = "elsewhere"
    insert_run(other)

    ids = {
        r["id"]
        for r in client.get(
            "/spa/runs", params={"session_id": "sess-scope-test", "exclude_chats": "true"}
        ).json()
    }
    assert child.id in ids        # spawned by this chat
    assert other.id not in ids    # a different conversation's run
    assert chat.id not in ids     # the chat turn itself (exclude_chats)


def test_spa_rejects_cross_site_fetch(client):
    r = client.post(
        "/spa/schedules",
        data={"name": "evil", "agent": "research", "cron": "0 9 * * *"},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert r.status_code == 403


def test_spa_rejects_foreign_origin(client):
    # Older browsers without Sec-Fetch-Site still send Origin on POSTs.
    r = client.post(
        "/spa/schedules",
        data={"name": "evil", "agent": "research", "cron": "0 9 * * *"},
        headers={"origin": "http://evil.example"},
    )
    assert r.status_code == 403


def test_spa_rejects_null_origin(client):
    r = client.post("/spa/runs", data={"agent": "research", "task": "x"},
                    headers={"origin": "null"})
    assert r.status_code == 403


def test_spa_allows_matching_origin(client):
    # TestClient requests carry Host: testserver.
    r = client.get("/spa/schedules", headers={"origin": "http://testserver"})
    assert r.status_code == 200


def _one_shot(run_at: str) -> ScheduleRecord:
    return ScheduleRecord.new(
        name="once", agent="research", task="t", cron="", run_at=run_at
    )


def test_schedule_dict_naive_run_at_does_not_raise():
    """create_runner stores naive ISO datetimes; comparing one against an
    aware `now` used to raise TypeError and 500 the whole schedules list."""
    future = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
    d = _schedule_dict(_one_shot(future.isoformat()))
    assert d["next_run"] == future.isoformat()


def test_schedule_dict_past_naive_run_at_is_none():
    past = (datetime.now() - timedelta(days=1)).replace(microsecond=0)
    assert _schedule_dict(_one_shot(past.isoformat()))["next_run"] is None


def test_schedule_dict_aware_run_at():
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    d = _schedule_dict(_one_shot(future.isoformat()))
    assert d["next_run"] == future.isoformat()


def test_schedule_dict_garbage_run_at_is_none():
    assert _schedule_dict(_one_shot("not-a-date"))["next_run"] is None


# ---------------------------------------------------------------------------
# Continue-a-run-as-a-chat: /spa/chats/{id} falls back to runs by session_id,
# so the chat view can resume a runner/spawn session with the right agent
# (previously the first message silently started a brand-new session).
# ---------------------------------------------------------------------------

def test_chat_detail_synthesized_from_run_session(client):
    import uuid

    from bran.persistence import RunRecord, insert_run

    session_id = f"sess-{uuid.uuid4().hex}"
    run = RunRecord.new(agent="finance-news", task="Morning briefing on FX markets", source="runner")
    run.session_id = session_id
    run.status = "completed"
    insert_run(run)

    r = client.get(f"/spa/chats/{session_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == session_id
    assert body["agent"] == "finance-news"
    assert body["title"].startswith("Morning briefing")


def test_chat_detail_unknown_id_still_404(client):
    assert client.get("/spa/chats/no-such-session").status_code == 404
