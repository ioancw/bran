"""HTTP tests for the bearer-auth /api surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bran.api import build_app

AUTH = {"Authorization": "Bearer test-token"}  # set in conftest.py


@pytest.fixture(scope="module")
def client():
    with TestClient(build_app(enable_scheduler=False)) as c:
        yield c


def test_api_requires_token(client):
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers=AUTH).status_code == 200


def test_background_run_unknown_agent_is_404_and_leaves_no_row(client):
    """Regression: this used to insert the run row first, swallow the KeyError
    in the background task, and leave the row stuck 'pending' forever."""
    from bran.persistence import list_runs

    before = {r.id for r in list_runs(limit=500)}
    r = client.post(
        "/api/agents/definitely-not-an-agent/run",
        json={"task": "x", "background": True},
        headers=AUTH,
    )
    assert r.status_code == 404
    after = {r.id for r in list_runs(limit=500)}
    assert after == before


def test_foreground_run_unknown_agent_is_404(client):
    r = client.post(
        "/api/agents/definitely-not-an-agent/run",
        json={"task": "x"},
        headers=AUTH,
    )
    assert r.status_code == 404
