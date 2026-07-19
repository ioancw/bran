"""Tier-1 hardening: abandoned-stream finalization, Host allowlist,
fetch egress guard, scheduler misfire policy."""

from __future__ import annotations

import asyncio

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi.testclient import TestClient

import bran.notify as notify
import bran.runner as runner
from bran.api import build_app
from bran.persistence import ScheduleRecord, list_runs
from bran.scheduler import _add_job
from bran.tools.documents import _reject_non_public, fetch_url


@pytest.fixture()
def client():
    with TestClient(build_app(enable_scheduler=False)) as c:
        yield c


# ---------------------------------------------------------------------------
# Abandoned stream → terminal run status (runner._drive GeneratorExit arm)
# ---------------------------------------------------------------------------

def test_abandoned_stream_reaches_terminal_status(monkeypatch):
    """A consumer that walks away from stream_agent mid-run (browser dropped
    the SSE) must not leave the run row stuck 'running' until next restart."""

    def fake_query(*, prompt, options):
        async def gen():
            yield object()
            yield object()  # never reached — consumer abandons after the first

        return gen()

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(notify, "_notifiers", [])

    async def main():
        agen = runner.stream_agent("research", "abandon-me")
        await agen.__anext__()  # first message arrives, run is live
        await agen.aclose()  # consumer walks away

    asyncio.run(main())

    rec = next(r for r in list_runs(limit=10) if r.task == "abandon-me")
    assert rec.status == "cancelled"
    assert rec.ended_at is not None
    assert "abandoned" in (rec.error or "")


# ---------------------------------------------------------------------------
# TrustedHost allowlist (DNS-rebinding defence)
# ---------------------------------------------------------------------------

def test_unknown_host_header_rejected(client):
    r = client.get("/healthz", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_allowlisted_host_accepted(client):
    # TestClient sends Host: testserver, allowlisted via conftest env.
    assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# fetch egress guard (SSRF / exfiltration)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/spa/runs",
        "http://10.0.0.8/feed",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ],
)
def test_egress_guard_blocks_non_public(url):
    err = asyncio.run(_reject_non_public(url))
    assert err is not None and "non-public" in err


def test_egress_guard_allows_public_ip():
    # Literal public IP — no DNS needed, no request made.
    assert asyncio.run(_reject_non_public("http://1.1.1.1/feed")) is None


def test_egress_guard_env_override(monkeypatch):
    monkeypatch.setenv("BRAN_FETCH_ALLOW_PRIVATE", "1")
    assert asyncio.run(_reject_non_public("http://127.0.0.1/")) is None


def test_fetch_url_refuses_loopback():
    res = asyncio.run(fetch_url.handler({"url": "http://127.0.0.1:1/x"}))
    text = res["content"][0]["text"]
    assert text.startswith("Error:")
    assert "non-public" in text


# ---------------------------------------------------------------------------
# Scheduler misfire policy
# ---------------------------------------------------------------------------

def test_one_shot_survives_downtime_cron_gets_an_hour():
    """One-shots get unlimited misfire grace (a past-due one fires on restart
    instead of zombifying); cron jobs coalesce fires missed within an hour."""
    sched = AsyncIOScheduler()
    once = ScheduleRecord.new(
        name="ms-once", agent="research", task="t", cron="", run_at="2030-01-01T09:00:00"
    )
    cron = ScheduleRecord.new(name="ms-cron", agent="research", task="t", cron="0 9 * * mon")
    _add_job(sched, once)
    _add_job(sched, cron)
    jobs = {j.name: j for j in sched.get_jobs()}
    assert jobs["ms-once"].misfire_grace_time is None
    assert jobs["ms-cron"].misfire_grace_time == 3600
    assert jobs["ms-cron"].coalesce is True
