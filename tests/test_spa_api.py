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
