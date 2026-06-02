"""Both run paths share one lifecycle: persistence + completion notification.

These stub the SDK `query` so they run without the (WSL-only) agent backend.
The point is the wiring — that `stream_agent` fires `notify_completion`, which
previously it silently did not (issue #3).
"""

from __future__ import annotations

import asyncio

import bran.notify as notify
from bran.runner import run_agent, stream_agent

# Sentinel messages the fake stream yields. They aren't real SDK message types,
# so _absorb_message ignores them — fine here, we only care about lifecycle.
_SENT = [object(), object()]


def _fake_query(*, prompt, options):
    async def gen():
        for m in _SENT:
            yield m

    return gen()


def _capture_notifier(monkeypatch):
    """Swap in a fresh notifier list and return the list runs get appended to."""
    seen: list = []
    monkeypatch.setattr(notify, "_notifiers", [lambda rec: seen.append(rec)])
    monkeypatch.setattr("bran.runner.query", _fake_query)
    return seen


def test_run_agent_fires_notifier(monkeypatch):
    seen = _capture_notifier(monkeypatch)
    rec = asyncio.run(run_agent("research", "hi"))
    assert [r.id for r in seen] == [rec.id]


def test_stream_agent_passes_messages_and_fires_notifier(monkeypatch):
    seen = _capture_notifier(monkeypatch)

    async def main():
        return [m async for m in stream_agent("research", "hi")]

    msgs = asyncio.run(main())
    # Messages flow through untouched...
    assert msgs == _SENT
    # ...and the completion notifier fired for the streaming path too.
    assert len(seen) == 1
    assert seen[0].agent == "research"
