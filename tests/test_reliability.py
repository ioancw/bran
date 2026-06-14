"""Tier-1 reliability behaviours: run timeout, runner retry, delta mode,
verification verdict parsing, named API tokens, and the artifacts table."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    add_artifact,
    get_schedule,
    insert_run,
    insert_schedule,
    list_artifacts,
    update_run,
    update_schedule,
    utcnow_iso,
)


# ---------------------------------------------------------------------------
# Run timeout
# ---------------------------------------------------------------------------

def test_run_timeout_marks_run_failed(monkeypatch):
    """A query() stream that never yields is aborted by BRAN_RUN_TIMEOUT and
    the run is failed with a clean timeout message (no traceback noise)."""
    import bran.runner as runner_mod
    from bran.runner import RunTimeoutError, _drive

    async def hung_query(prompt, options):  # never yields, never returns
        await asyncio.sleep(3600)
        yield  # pragma: no cover

    monkeypatch.setattr(runner_mod, "query", hung_query)
    # Settings is a frozen dataclass — patch via object.__setattr__.
    object.__setattr__(runner_mod.SETTINGS, "run_timeout_s", 1)
    try:
        record = RunRecord.new(agent="research", task="t")
        record.status = "running"
        insert_run(record)

        async def go():
            async for _ in _drive(record, "t", options=None):
                pass

        with pytest.raises(RunTimeoutError):
            asyncio.run(go())
        assert record.status == "failed"
        assert "timed out" in (record.error or "")
        assert "Traceback" not in (record.error or "")
    finally:
        object.__setattr__(runner_mod.SETTINGS, "run_timeout_s", 3600)


def test_error_truncation():
    from bran.runner import _MAX_ERROR_CHARS, _truncate_error

    assert _truncate_error("short") == "short"
    long = "x" * (_MAX_ERROR_CHARS + 100)
    out = _truncate_error(long)
    assert len(out) < len(long)
    assert out.endswith("[… error truncated …]")


# ---------------------------------------------------------------------------
# Runner retry
# ---------------------------------------------------------------------------

def test_fire_schedules_retry_on_failure(monkeypatch):
    """A failed scheduled run queues a one-shot retry with backoff."""
    import bran.scheduler as sched_mod

    name = f"r-{uuid.uuid4().hex[:6]}"
    rec = ScheduleRecord.new(name=name, agent="research", task="t", cron="0 8 * * *")
    insert_schedule(rec)

    async def failing_run_agent(*a, **k):
        raise RuntimeError("boom")

    captured: dict = {}

    def fake_schedule_retry(*args) -> bool:
        captured["args"] = args
        return True

    monkeypatch.setattr(sched_mod, "run_agent", failing_run_agent)
    monkeypatch.setattr(sched_mod, "_schedule_retry", fake_schedule_retry)
    asyncio.run(sched_mod._fire("research", "t", name, None, rec.id, False, 0))
    # attempt bumps to 1 in the queued retry
    assert captured["args"][-1] == 1


def test_retry_skipped_when_runner_paused(monkeypatch):
    """A retry firing after the user paused the runner must not run."""
    import bran.scheduler as sched_mod

    name = f"r-{uuid.uuid4().hex[:6]}"
    rec = ScheduleRecord.new(name=name, agent="research", task="t", cron="0 8 * * *")
    rec.enabled = False
    insert_schedule(rec)

    called = {"n": 0}

    async def should_not_run(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(sched_mod, "run_agent", should_not_run)
    asyncio.run(sched_mod._fire("research", "t", name, None, rec.id, False, 1))
    assert called["n"] == 0


def test_one_shot_not_disabled_while_retry_pending(monkeypatch):
    """A failed one-shot stays enabled while a retry is queued, and is only
    disabled once retries are exhausted."""
    import bran.scheduler as sched_mod

    name = f"r-{uuid.uuid4().hex[:6]}"
    rec = ScheduleRecord.new(name=name, agent="research", task="t", cron="",
                             run_at="2099-01-01T09:00:00")
    insert_schedule(rec)

    async def failing_run_agent(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(sched_mod, "run_agent", failing_run_agent)
    monkeypatch.setattr(sched_mod, "_schedule_retry", lambda *a: True)
    asyncio.run(sched_mod._fire("research", "t", name, None, rec.id, True, 0))
    assert get_schedule(name).enabled  # retry pending — still enabled

    # Retries exhausted (attempt == BRAN_RUNNER_RETRIES) → resolved, disabled.
    monkeypatch.setattr(sched_mod, "_schedule_retry", lambda *a: False)
    asyncio.run(
        asyncio.wait_for(
            sched_mod._fire("research", "t", name, None, rec.id, True,
                            sched_mod.SETTINGS.runner_retries),
            timeout=10,
        )
    )
    assert not get_schedule(name).enabled


# ---------------------------------------------------------------------------
# Delta mode
# ---------------------------------------------------------------------------

def test_delta_append_includes_previous_report():
    from bran.scheduler import _delta_append_system

    name = f"r-{uuid.uuid4().hex[:6]}"
    sched = ScheduleRecord.new(name=name, agent="research", task="t", cron="0 8 * * *")
    insert_schedule(sched)
    run = RunRecord.new(agent="research", task="t", schedule_id=sched.id, source="runner")
    run.status = "completed"
    run.result = "Yesterday: ACME up 4%."
    run.ended_at = utcnow_iso()
    insert_run(run)
    update_run(run)

    out = _delta_append_system(sched.id)
    assert out is not None
    assert "ACME up 4%" in out
    assert "NEW or CHANGED" in out


def test_delta_append_none_without_history():
    from bran.scheduler import _delta_append_system

    assert _delta_append_system(None) is None
    assert _delta_append_system(str(uuid.uuid4())) is None


# ---------------------------------------------------------------------------
# Verification verdict parsing (the critic itself is stubbed)
# ---------------------------------------------------------------------------

def test_verify_flags_roundtrip():
    name = f"r-{uuid.uuid4().hex[:6]}"
    rec = ScheduleRecord.new(name=name, agent="research", task="t",
                             cron="0 8 * * *", verify=True, delta=True)
    insert_schedule(rec)
    stored = get_schedule(name)
    assert stored.verify is True
    assert stored.delta is True
    # update preserves when None, changes when set
    update_schedule(name, agent="research", task="t", cron="0 8 * * *",
                    run_at=None, verify=None, delta=False)
    stored = get_schedule(name)
    assert stored.verify is True
    assert stored.delta is False


# ---------------------------------------------------------------------------
# Named API tokens
# ---------------------------------------------------------------------------

def test_parse_api_tokens(monkeypatch):
    from bran.config import _parse_api_tokens

    monkeypatch.setenv("BRAN_API_TOKEN", "tok-legacy")
    monkeypatch.setenv("BRAN_API_TOKENS", "ioan:tok-a, partner:tok-b,, bad-entry")
    tokens = _parse_api_tokens()
    assert tokens == {"tok-legacy": "api", "tok-a": "ioan", "tok-b": "partner"}


def test_api_run_attributed_to_token_name(monkeypatch):
    """A background /api run carries the token's name as its actor."""
    from fastapi.testclient import TestClient

    import bran.api as api_mod
    from bran.persistence import get_run

    object.__setattr__(api_mod.SETTINGS, "api_tokens", {"tok-a": "ioan"})

    async def fake_run_agent(*a, **k):
        return k.get("record")

    monkeypatch.setattr(api_mod, "run_agent", fake_run_agent)
    app = api_mod.build_app(enable_scheduler=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/agents/research/run",
            json={"task": "t", "background": True},
            headers={"Authorization": "Bearer tok-a"},
        )
        assert r.status_code == 200
        run_id = r.json()["run_id"]
    stored = get_run(run_id)
    assert stored.actor == "ioan"


# ---------------------------------------------------------------------------
# Artifacts table
# ---------------------------------------------------------------------------

def test_add_artifact_idempotent_and_listed():
    run = RunRecord.new(agent="research", task="t")
    insert_run(run)
    add_artifact(run.id, "/tmp/a.md")
    add_artifact(run.id, "/tmp/a.md")  # dupe — no error, no double row
    add_artifact(run.id, "/tmp/b.md")
    assert list_artifacts(run.id) == ["/tmp/a.md", "/tmp/b.md"]
