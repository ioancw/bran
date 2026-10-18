"""Deleting runs: finished runs go (with their subagent children, artifacts and
read state); live runs are refused one-by-one and skipped in a batch."""

from __future__ import annotations

from fastapi.testclient import TestClient

from bran.api import build_app
from bran.persistence import (
    RunRecord,
    add_artifact,
    get_run,
    insert_run,
    list_artifacts,
    set_output_starred,
)


def _run(status: str, parent: str | None = None, source: str = "manual") -> RunRecord:
    r = RunRecord.new(agent="research", task="t", source=source, parent_run_id=parent)
    r.status = status
    insert_run(r)
    return r


def test_delete_removes_run_children_artifacts_and_state():
    parent = _run("completed")
    child = _run("completed", parent=parent.id, source="subagent")
    add_artifact(parent.id, "/tmp/x.md")
    set_output_starred(parent.id, True)
    with TestClient(build_app(enable_scheduler=False)) as c:
        r = c.delete(f"/spa/runs/{parent.id}")
        assert r.status_code == 200 and r.json()["deleted"] == 1
    assert get_run(parent.id) is None and get_run(child.id) is None
    assert list_artifacts(parent.id) == []


def test_live_run_is_refused_and_unknown_is_404():
    with TestClient(build_app(enable_scheduler=False)) as c:
        # Created AFTER startup: the app's crash recovery fails any run that
        # was 'running' when it started, which is exactly what we don't want here.
        live = _run("running")
        assert c.delete(f"/spa/runs/{live.id}").status_code == 409
        assert c.delete("/spa/runs/nope").status_code == 404
    assert get_run(live.id) is not None


def test_batch_delete_skips_live_runs():
    a = _run("failed")
    b = _run("cancelled")
    with TestClient(build_app(enable_scheduler=False)) as c:
        live = _run("pending")  # after startup, see above
        r = c.post("/spa/runs/delete", data={"ids": ",".join([a.id, b.id, live.id, ""])})
    assert r.json() == {"deleted": 2, "skipped": 1}
    assert get_run(a.id) is None and get_run(b.id) is None and get_run(live.id) is not None
