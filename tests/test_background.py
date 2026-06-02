"""The fire-and-forget task tracker keeps a strong ref until completion."""

from __future__ import annotations

import asyncio

from bran.background import _background_tasks, spawn_background


def test_task_is_tracked_then_discarded():
    ran: list[int] = []

    async def work():
        await asyncio.sleep(0)
        ran.append(1)

    async def main():
        task = spawn_background(work(), name="unit")
        # Tracked while in flight so the loop's weak ref isn't the only one.
        assert task in _background_tasks
        await task
        return task

    task = asyncio.run(main())
    assert ran == [1]
    # done-callback removed it from the live set.
    assert task not in _background_tasks


def test_failing_task_is_still_discarded():
    async def boom():
        raise RuntimeError("nope")

    async def main():
        task = spawn_background(boom(), name="boom")
        try:
            await task
        except RuntimeError:
            pass
        return task

    task = asyncio.run(main())
    assert task not in _background_tasks
