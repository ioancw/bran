"""Background-run cancellation: registry helper + terminal status in the runner."""

from __future__ import annotations

import asyncio

import pytest

import bran.notify as notify
import bran.runner as runner
from bran.background import _background_tasks, cancel_background, spawn_background
from bran.persistence import RunRecord, get_run, insert_run


def test_cancel_background_matches_by_run_id_suffix():
    async def main():
        async def forever():
            await asyncio.sleep(60)

        task = spawn_background(forever(), name="spawn:RUN-abc")
        await asyncio.sleep(0)  # let it start

        # Non-matching id is a no-op.
        assert cancel_background("RUN-xyz") == 0
        # Matching id cancels exactly one task.
        assert cancel_background("RUN-abc") == 1
        with pytest.raises(asyncio.CancelledError):
            await task
        return task

    task = asyncio.run(main())
    assert task not in _background_tasks  # done-callback cleaned it up


def test_cancelled_run_reaches_terminal_status(monkeypatch):
    started = asyncio.Event()

    def fake_query(*, prompt, options):
        async def gen():
            started.set()
            await asyncio.sleep(60)  # block until cancelled
            yield object()

        return gen()

    monkeypatch.setattr(runner, "query", fake_query)
    monkeypatch.setattr(notify, "_notifiers", [])  # silence notifiers

    rec = RunRecord.new(agent="research", task="hi")
    insert_run(rec)

    async def main():
        task = spawn_background(
            runner.run_agent("research", "hi", record=rec), name=f"spawn:{rec.id}"
        )
        await started.wait()
        assert cancel_background(rec.id) == 1
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())

    refreshed = get_run(rec.id)
    assert refreshed is not None
    assert refreshed.status == "cancelled"
    assert refreshed.ended_at is not None  # finalised, not stuck "running"
