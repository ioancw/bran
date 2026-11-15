"""The hardening / data-layer batch: untrusted file headers, webhook payload,
project deletion, stored transcripts, migrations, retention, catch-up, and the
shared runner-creation rules."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from bran.api import build_app
from bran.persistence import (
    ChatRecord,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    get_chat,
    get_run,
    get_run_events,
    get_schedule,
    insert_project,
    insert_run,
    insert_schedule,
    list_runs,
    save_run_events,
    set_setting,
    upsert_chat,
    utcnow_iso,
)

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(scope="module")
def client():
    with TestClient(build_app(enable_scheduler=False)) as c:
        yield c


def _project(name: str = "P") -> ProjectRecord:
    p = ProjectRecord.new(name=f"{name}-{uuid.uuid4().hex[:6]}")
    insert_project(p)
    return p


def _name(prefix: str = "r") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# --- 1. untrusted files -------------------------------------------------------

def test_project_html_is_served_sandboxed(client):
    p = _project()
    files = [
        ("files", ("evil.html", b"<script>fetch('/spa/runs')</script>", "text/html")),
        ("files", ("paper.pdf", b"%PDF-1.4", "application/pdf")),
    ]
    assert client.post(f"/spa/projects/{p.id}/files", files=files).status_code == 200

    r = client.get(f"/spa/projects/{p.id}/files/evil.html")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "sandbox allow-scripts allow-modals"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("inline")

    # A sandboxed document cannot host the browser's PDF viewer.
    r = client.get(f"/spa/projects/{p.id}/files/paper.pdf")
    assert "content-security-policy" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"

    assert client.get(f"/spa/projects/{p.id}/files/evil.html?download=1").headers[
        "content-disposition"
    ].startswith("attachment")
    # A traversal attempt never reaches a file outside the folder (the path no
    # longer matches this route at all, so the app shell answers instead).
    r = client.get(f"/spa/projects/{p.id}/files/..%2F..%2Fbran.sqlite")
    assert not r.content.startswith(b"SQLite format 3")
    assert r.status_code in (400, 404, 503) or "text/html" in r.headers["content-type"]


def test_file_routes_need_a_real_project(client):
    assert client.delete("/spa/projects/nope/files/x.txt").status_code == 404
    assert client.get("/spa/projects/nope/memory").status_code == 404


def test_memory_can_only_be_removed_through_its_own_project(client):
    a, b = _project("A"), _project("B")
    entry = client.post(f"/spa/projects/{a.id}/memory", data={"text": "fact"}).json()
    assert client.delete(f"/spa/projects/{b.id}/memory/{entry['id']}").status_code == 404
    assert client.delete(f"/spa/projects/{a.id}/memory/{entry['id']}").status_code == 200


def test_upload_over_the_cap_is_refused(client, monkeypatch):
    import bran.web.spa_routes.common as common

    monkeypatch.setattr(common, "MAX_UPLOAD_BYTES", 10)
    p = _project()
    r = client.post(f"/spa/projects/{p.id}/files", files=[("files", ("big.txt", b"x" * 64, "text/plain"))])
    assert r.status_code == 413


def test_runs_limit_is_bounded(client):
    assert client.get("/spa/runs?limit=-1").status_code == 422
    assert client.get("/spa/runs?limit=100000").status_code == 422
    assert client.get("/spa/runs?limit=5").status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/spa/runs"),
        ("post", "/spa/schedules"),
        ("post", "/spa/projects"),
        ("post", "/spa/settings"),
        ("post", "/spa/chat/stream"),
        ("delete", "/spa/projects/x"),
        ("delete", "/spa/runs/x"),
        ("delete", "/spa/chats/x"),
        ("delete", "/spa/schedules/x"),
    ],
)
def test_cross_site_writes_are_refused(client, method, path):
    r = getattr(client, method)(path, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


# --- 3. webhook payload -------------------------------------------------------

def test_webhook_payload_has_no_metadata_or_transcript():
    from bran.notify import webhook_payload

    rec = RunRecord.new(agent="research", task="t", source="runner")
    rec.status, rec.result = "completed", "the report"
    rec.metadata = {"events": [{"type": "tool_result", "text": "SECRET PAGE"}], "depth": 1}
    payload = webhook_payload(rec, alert=False)
    assert "metadata" not in payload and "SECRET" not in repr(payload)
    assert payload["result"] == "the report" and payload["alert"] is False
    assert payload["summary"]


# --- 4. deleting a project ----------------------------------------------------

def test_deleting_a_project_detaches_runners_and_drops_its_summary_runner(client):
    from bran.project_summary import enable_daily_summary

    p = _project("Doomed")
    summary = enable_daily_summary(p)
    kept = ScheduleRecord.new(name=_name("kept"), agent="research", task="t", cron="0 9 * * *", project_id=p.id)
    insert_schedule(kept)
    run = RunRecord.new(agent="research", task="t", project_id=p.id)
    run.status = "completed"
    insert_run(run)
    chat = ChatRecord(id="c-" + uuid.uuid4().hex, title="t", agent="orchestrator", project_id=p.id)
    upsert_chat(chat)

    assert client.delete(f"/spa/projects/{p.id}").status_code == 200
    assert get_schedule(summary.name) is None
    assert get_schedule(kept.name).project_id is None
    assert get_run(run.id).project_id is None
    assert get_chat(chat.id).project_id is None
    assert client.delete(f"/spa/projects/{p.id}").status_code == 404


# --- 6. stored transcripts live in their own table ----------------------------

def test_events_are_stored_apart_from_the_run_row(client):
    rec = RunRecord.new(agent="research", task="t")
    rec.status = "completed"
    rec.metadata = {"events": [{"type": "text", "text": "hello"}], "depth": 2}
    insert_run(rec)
    assert get_run_events(rec.id) == [{"type": "text", "text": "hello"}]
    assert get_run(rec.id).metadata == {"depth": 2}
    listed = next(r for r in client.get("/spa/runs?limit=50").json() if r["id"] == rec.id)
    assert "events" not in listed["metadata"]
    assert [e["type"] for e in client.get(f"/spa/runs/{rec.id}/transcript").json()["events"]] == ["user", "text"]


def test_deleting_a_chat_removes_its_turns_but_not_its_spawns(client):
    sid = "s-" + uuid.uuid4().hex
    upsert_chat(ChatRecord(id=sid, title="t", agent="orchestrator"))
    turn = RunRecord.new(agent="orchestrator", task="hi", source="chat")
    turn.session_id, turn.status = sid, "completed"
    insert_run(turn)
    save_run_events(turn.id, [{"type": "text", "text": "x"}])
    spawn = RunRecord.new(agent="research", task="dig", source="spawn", parent_run_id=turn.id)
    spawn.status = "completed"
    insert_run(spawn)
    set_setting("chat_history_db:" + sid, "1")

    assert client.delete(f"/spa/chats/{sid}").status_code == 200
    assert get_run(turn.id) is None and get_run_events(turn.id) == []
    assert get_run(spawn.id) is not None
    from bran.persistence import get_setting

    assert get_setting("chat_history_db:" + sid, "") == ""


# --- 9. retention ------------------------------------------------------------

def test_prune_drops_old_transcripts_and_keeps_the_run():
    from bran.persistence import prune_run_events

    old, new = RunRecord.new(agent="a", task="t"), RunRecord.new(agent="a", task="t")
    for rec, age in ((old, 400), (new, 1)):
        rec.status, rec.result = "completed", "kept"
        rec.ended_at = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
        insert_run(rec)
        save_run_events(rec.id, [{"type": "text", "text": "x"}])
    assert prune_run_events(0) == 0  # 0 = keep forever
    assert prune_run_events(180) >= 1
    assert get_run_events(old.id) == [] and get_run(old.id).result == "kept"
    assert get_run_events(new.id) != []


def test_prune_uploads_removes_only_stale_attachments():
    from bran.project_files import prune_uploads, save_attachment

    stale = save_attachment("old.txt", b"x")["path"]
    fresh = save_attachment("new.txt", b"x")["path"]
    long_ago = time.time() - 90 * 86400
    os.utime(stale, (long_ago, long_ago))
    assert prune_uploads(30) >= 1
    assert not os.path.exists(stale) and os.path.exists(fresh)


# --- 18. migrations ----------------------------------------------------------

def _legacy_db(path) -> None:
    """A database as an early bran wrote it: no project/source/schedule
    columns, the transcript inside runs.metadata, user_version 0."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (id TEXT PRIMARY KEY, agent TEXT NOT NULL, task TEXT NOT NULL,
            status TEXT NOT NULL, session_id TEXT, parent_run_id TEXT, result TEXT, error TEXT,
            total_cost_usd REAL, num_turns INTEGER, duration_ms INTEGER,
            started_at TEXT NOT NULL, ended_at TEXT, metadata TEXT);
        CREATE TABLE schedules (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, agent TEXT NOT NULL,
            task TEXT NOT NULL, cron TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL);
        CREATE TABLE chats (id TEXT PRIMARY KEY, title TEXT NOT NULL, agent TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO chats VALUES ('sess-1', 't', 'orchestrator', 'x', 'x');
        INSERT INTO runs (id, agent, task, status, session_id, started_at, metadata) VALUES
            ('r1', 'orchestrator', 'hi', 'completed', 'sess-1', '2026-01-01T00:00:00+00:00',
             '{"events": [{"type": "text", "text": "old"}], "artifacts": ["/tmp/a.md"], "depth": 1}'),
            ('r2', 'research', 'dig', 'completed', NULL, '2026-01-02T00:00:00+00:00', 'not json');
        INSERT INTO schedules VALUES ('s1', 'legacy', 'research', 't', '0 9 * * *', 1, 'x');
        """
    )
    conn.close()


def test_a_legacy_database_is_migrated_once(tmp_path):
    import json

    from bran.persistence import _MIGRATIONS, _bring_up_to_date

    path = tmp_path / "legacy.sqlite"
    _legacy_db(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _bring_up_to_date(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS)
    r1 = conn.execute("SELECT * FROM runs WHERE id = 'r1'").fetchone()
    assert r1["source"] == "chat" and r1["project_id"] is None
    assert json.loads(r1["metadata"]) == {"artifacts": ["/tmp/a.md"], "depth": 1}
    stored = conn.execute("SELECT events FROM run_events WHERE run_id = 'r1'").fetchone()
    assert json.loads(stored["events"]) == [{"type": "text", "text": "old"}]
    # A row whose metadata is not JSON survives untouched.
    r2 = conn.execute("SELECT * FROM runs WHERE id = 'r2'").fetchone()
    assert r2["source"] == "manual" and r2["metadata"] == "not json"
    assert conn.execute("SELECT path FROM artifacts WHERE run_id = 'r1'").fetchone()["path"] == "/tmp/a.md"
    sched = conn.execute("SELECT * FROM schedules").fetchone()
    assert sched["verify"] == 0 and sched["alert"] == "" and sched["run_at"] is None
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_runs_session", "idx_runs_parent", "idx_runs_schedule", "idx_runs_tool_use",
            "idx_chats_project"} <= indexes

    # A second start does nothing (a changed row is not re-migrated).
    conn.execute("UPDATE runs SET source = NULL WHERE id = 'r2'")
    _bring_up_to_date(conn)
    assert conn.execute("SELECT source FROM runs WHERE id = 'r2'").fetchone()["source"] is None
    conn.close()


def test_a_fresh_database_starts_at_the_latest_version(tmp_path):
    from bran.persistence import _MIGRATIONS, _bring_up_to_date

    conn = sqlite3.connect(tmp_path / "fresh.sqlite", isolation_level=None)
    conn.row_factory = sqlite3.Row
    _bring_up_to_date(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS)
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(chats)")}
    assert {"project_id", "archived_at"} <= cols
    conn.close()


# --- 2. catch-up -------------------------------------------------------------

def test_last_due_finds_the_most_recent_slot_in_the_window():
    from bran.scheduler import last_due

    now = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)  # a Monday
    due = last_due("0 * * * *", now - timedelta(hours=3, minutes=30), now)
    assert due is not None and due.astimezone(timezone.utc).minute == 0
    assert now - due <= timedelta(hours=1)
    assert last_due("0 * * * *", now - timedelta(minutes=20), now - timedelta(minutes=5)) is None


def _runner(cron: str, *, created_hours_ago: float = 100, **kw) -> ScheduleRecord:
    rec = ScheduleRecord.new(name=_name("cu"), agent="research", task="t", cron=cron, **kw)
    rec.created_at = (datetime.now(timezone.utc) - timedelta(hours=created_hours_ago)).isoformat()
    insert_schedule(rec)
    return rec


def test_catch_up_plans_a_run_for_a_slot_missed_while_down(monkeypatch):
    import bran.scheduler as sched

    now = datetime.now(timezone.utc)
    hourly = _runner("* * * * *")                               # due every minute
    recent = _runner("* * * * *", created_hours_ago=0)          # created after the outage
    ran = _runner("* * * * *")
    done = RunRecord.new(agent="research", task="t", source="runner", schedule_id=ran.id)
    done.started_at = (now + timedelta(seconds=1)).isoformat()  # already ran since
    insert_run(done)
    paused = _runner("* * * * *")
    from bran.persistence import set_schedule_enabled

    set_schedule_enabled(paused.name, False)

    set_setting(sched._ALIVE_KEY, (now - timedelta(hours=2)).isoformat())
    names = {e["name"] for e in sched.plan_catch_up(now)["caught_up"]}
    assert hourly.name in names
    assert not names & {recent.name, ran.name, paused.name}

    # No heartbeat on record → nothing is assumed missed.
    set_setting(sched._ALIVE_KEY, "")
    assert sched.plan_catch_up(now) == {"caught_up": [], "skipped": []}


def test_catch_up_skips_slots_older_than_the_window():
    import bran.scheduler as sched

    now = datetime.now(timezone.utc)
    # Due once a year, at a minute that fell 3 days ago — outside the 12h window.
    then = (now - timedelta(days=3)).astimezone()
    yearly = _runner(f"{then.minute} {then.hour} {then.day} {then.month} *")
    set_setting(sched._ALIVE_KEY, (now - timedelta(days=5)).isoformat())
    report = sched.plan_catch_up(now)
    assert yearly.name in {e["name"] for e in report["skipped"]}
    assert yearly.name not in {e["name"] for e in report["caught_up"]}
    set_setting(sched._ALIVE_KEY, "")


def test_health_reports_the_scheduler(client):
    s = client.get("/spa/health").json()["scheduler"]
    assert set(s) >= {"running", "alive_at", "caught_up", "skipped", "catchup_hours"}


# --- 17. one set of rules for creating a runner --------------------------------

def test_duplicate_runner_name_is_a_conflict_on_both_surfaces(client):
    name = _name("dup")
    body = {"name": name, "agent": "research", "task": "t", "cron": "0 9 * * *"}
    assert client.post("/spa/schedules", data=body).status_code == 200
    assert client.post("/spa/schedules", data=body).status_code == 409
    assert client.post("/api/schedules", json=body, headers=AUTH).status_code == 409


def test_api_schedule_is_validated_like_the_form(client):
    base = {"name": _name("api"), "agent": "research", "task": "t", "cron": "0 9 * * *"}
    assert client.post("/api/schedules", json={**base, "cron": "whenever"}, headers=AUTH).status_code == 400
    assert client.post("/api/schedules", json={**base, "agent": "nobody"}, headers=AUTH).status_code == 400
    assert client.post("/api/schedules", json={**base, "name": "a/b"}, headers=AUTH).status_code == 400
    r = client.post("/api/schedules", json={**base, "cron": "every weekday at 9am"}, headers=AUTH)
    assert r.status_code == 201 and len(r.json()["cron"].split()) == 5
    assert client.delete(f"/api/schedules/{base['name']}", headers=AUTH).status_code == 200


def test_chat_tool_refuses_a_duplicate_runner():
    from bran.tools.schedules import create_runner

    name = _name("tool")
    args = {"name": name, "agent": "research", "task": "t", "schedule": "every day at 9am"}
    first = asyncio.run(create_runner.handler(args))
    assert "Created runner" in first["content"][0]["text"]
    second = asyncio.run(create_runner.handler(args))
    assert second["content"][0]["text"].startswith("Error:") and "already exists" in second["content"][0]["text"]


def test_a_run_that_fails_before_it_starts_is_not_left_pending(client, monkeypatch):
    import bran.web.spa_routes.common as common

    async def boom(agent, task, *, record=None, **kw):
        raise ValueError("bad options")

    monkeypatch.setattr(common, "run_agent", boom)
    rid = client.post("/spa/runs", data={"agent": "research", "task": "t"}).json()["id"]
    for _ in range(50):
        if get_run(rid).status == "failed":
            break
        time.sleep(0.02)
    rec = get_run(rid)
    assert rec.status == "failed" and "bad options" in rec.error


def test_find_subagent_run_still_matches_by_tool_use_id():
    from bran.persistence import find_subagent_run

    child = RunRecord.new(agent="research", task="t", source="subagent")
    child.metadata["tool_use_id"] = "tu-" + uuid.uuid4().hex
    insert_run(child)
    assert find_subagent_run(child.metadata["tool_use_id"]).id == child.id
    assert list_runs(parent_run_id="nope") == []
    assert utcnow_iso()
