"""Alert-on-threshold (sensing runners): the schedules.alert significance bar,
the ALERT-marker contract between scheduler prompt and notifiers, and the
tri-state update semantics (None = keep, "" = clear, text = set)."""

from __future__ import annotations

import uuid

from bran.notify import ALERT_MARKER, is_alert
from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    get_schedule,
    insert_schedule,
    update_schedule,
)
from bran.scheduler import _alert_append_system

# ---------------------------------------------------------------------------
# Persistence round-trip + tri-state update
# ---------------------------------------------------------------------------


def test_alert_roundtrip_and_tristate_update():
    name = f"r-{uuid.uuid4().hex[:6]}"
    rec = ScheduleRecord.new(name=name, agent="research", task="t",
                             cron="*/30 * * * *", alert="GBP moves >1% intraday")
    insert_schedule(rec)
    assert get_schedule(name).alert == "GBP moves >1% intraday"

    # None = leave unchanged
    update_schedule(name, agent="research", task="t", cron="*/30 * * * *",
                    run_at=None, alert=None)
    assert get_schedule(name).alert == "GBP moves >1% intraday"

    # a new bar replaces the old one
    update_schedule(name, agent="research", task="t", cron="*/30 * * * *",
                    run_at=None, alert="BoE acts outside a scheduled meeting")
    assert get_schedule(name).alert == "BoE acts outside a scheduled meeting"

    # "" = clear
    update_schedule(name, agent="research", task="t", cron="*/30 * * * *",
                    run_at=None, alert="")
    assert get_schedule(name).alert == ""


def test_alert_defaults_off():
    name = f"r-{uuid.uuid4().hex[:6]}"
    insert_schedule(ScheduleRecord.new(name=name, agent="research", task="t",
                                       cron="0 8 * * *"))
    assert get_schedule(name).alert == ""


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------


def test_alert_append_system_carries_bar_and_marker():
    out = _alert_append_system("oil crosses $100")
    assert "oil crosses $100" in out
    assert ALERT_MARKER in out
    # Both branches of the contract are spelled out for the agent.
    assert "Bar crossed" in out and "not crossed" in out


# ---------------------------------------------------------------------------
# Marker detection (notify contract)
# ---------------------------------------------------------------------------


def _run(status: str, result: str) -> RunRecord:
    r = RunRecord.new(agent="research", task="t")
    r.status = status
    r.result = result
    return r


def test_is_alert_detects_leading_marker_only():
    assert is_alert(_run("completed", f"{ALERT_MARKER}: cable fell 1.4%\n…"))
    # leading whitespace tolerated
    assert is_alert(_run("completed", f"\n  {ALERT_MARKER}: crossed"))
    # marker mid-report (e.g. quoting the instructions) is NOT an alert
    assert not is_alert(_run("completed", f"Quiet day. (No {ALERT_MARKER} today.)"))
    assert not is_alert(_run("completed", "Nothing crossed the alert bar."))
    # failed runs never alert, whatever the text says
    assert not is_alert(_run("failed", f"{ALERT_MARKER}: bogus"))
    assert not is_alert(_run("completed", ""))


def test_spa_update_distinguishes_absent_from_empty_alert():
    """Regression: FastAPI coerces an empty Optional form field to None, which
    made 'clear the bar' (alert=) indistinguishable from 'leave unchanged'
    (field absent). The endpoint must honour both."""
    from fastapi.testclient import TestClient

    from bran.api import build_app

    name = f"r-{uuid.uuid4().hex[:6]}"
    with TestClient(build_app(enable_scheduler=False)) as client:
        r = client.post("/spa/schedules", data={
            "name": name, "agent": "research", "cron": "0 8 * * *",
            "task": "t", "alert": "oil crosses $100",
        })
        assert r.status_code == 200 and r.json()["alert"] == "oil crosses $100"

        base = {"agent": "research", "cron": "0 8 * * *", "task": "t"}
        # field absent → unchanged
        r = client.post(f"/spa/schedules/{name}", data=base)
        assert r.json()["alert"] == "oil crosses $100"
        # empty field → cleared
        r = client.post(f"/spa/schedules/{name}", data={**base, "alert": ""})
        assert r.json()["alert"] == ""


def test_webhook_payload_flags_alert(monkeypatch):
    """The webhook notifier marks alert runs and escalates the ntfy headers."""
    import bran.notify as notify

    captured: dict = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            captured["headers"] = headers

    import httpx

    monkeypatch.setenv("BRAN_NOTIFY_WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    import asyncio

    asyncio.run(notify.webhook_notifier(_run("completed", f"{ALERT_MARKER}: it happened")))
    assert captured["payload"]["alert"] is True
    assert captured["headers"]["Priority"] == "urgent"

    asyncio.run(notify.webhook_notifier(_run("completed", "calm seas")))
    assert captured["payload"]["alert"] is False
    assert captured["headers"]["Priority"] == "default"
