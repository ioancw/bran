"""bran.live pub/sub: replay-then-live semantics behind /spa/runs/{id}/stream."""

from __future__ import annotations

import asyncio

import bran.notify as notify
from bran import live


def test_subscribe_unknown_run_ends_immediately():
    async def main():
        return [ev async for ev in live.subscribe("nope")]

    assert asyncio.run(main()) == []


def test_replay_then_live_then_close():
    async def main():
        live.publish("r1", [{"type": "text", "text": "a"}])
        live.publish("r1", [{"type": "text", "text": "b"}])

        got: list[dict] = []

        async def consume():
            async for ev in live.subscribe("r1"):
                got.append(ev)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)  # let the subscriber attach + replay
        live.publish("r1", [{"type": "text", "text": "c"}])
        live.close("r1")
        await asyncio.wait_for(task, timeout=2)
        return got

    got = asyncio.run(main())
    assert [e["text"] for e in got] == ["a", "b", "c"]
    assert not live.is_live("r1")


def test_close_is_idempotent_and_empty_publish_ignored():
    live.publish("r2", [])
    assert not live.is_live("r2")  # empty publish creates nothing
    live.close("r2")  # no-op, no raise
    live.close("r2")


def test_buffer_is_capped():
    try:
        for i in range(live._MAX_BUFFER + 50):
            live.publish("r3", [{"type": "text", "text": str(i)}])
        assert len(live._streams["r3"].events) == live._MAX_BUFFER
        # Oldest dropped, newest kept.
        assert live._streams["r3"].events[-1]["text"] == str(live._MAX_BUFFER + 49)
    finally:
        live.close("r3")


def test_runner_closes_stream_on_finish(monkeypatch):
    """Every run path closes its live stream when it ends (the finally hook)."""
    from bran.runner import run_agent

    def _fake_query(*, prompt, options):
        async def gen():
            yield object()  # not a real SDK message — ignored by converters

        return gen()

    monkeypatch.setattr(notify, "_notifiers", [])
    monkeypatch.setattr("bran.runner.query", _fake_query)
    rec = asyncio.run(run_agent("research", "hi"))
    assert not live.is_live(rec.id)
