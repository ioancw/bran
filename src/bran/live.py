"""In-process live event streams for runs — the pub/sub behind "watch a
background run execute".

`runner._drive` publishes each SDK message (converted to unified chat events)
under the run's id; the SPA's `GET /spa/runs/{id}/stream` endpoint subscribes
and relays them as SSE. A per-run replay buffer means a subscriber attaching
mid-run still gets the full transcript so far — no gap, no duplicates (the
queue is registered and the buffer snapshotted in the same synchronous block).

Single-process by design (matches bran's asyncio runtime: chat, scheduler and
spawns all share the serve loop). If a run isn't in the registry — server
restarted, or it finished — subscribers get nothing and the endpoint falls
back to "fetch the stored transcript".
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

# Cap the replay buffer so a pathologically chatty run can't hoard memory.
# A subscriber attaching after 2000 events misses the oldest ones — rare, and
# the canonical transcript is refetched at run end anyway.
_MAX_BUFFER = 2000


class _Stream:
    __slots__ = ("events", "queues")

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.queues: set[asyncio.Queue[list[dict[str, Any]] | None]] = set()


_streams: dict[str, _Stream] = {}


def publish(run_id: str, events: list[dict[str, Any]]) -> None:
    """Append events to the run's buffer and fan them out to subscribers.

    Creates the stream lazily on first publish, so `is_live` doubles as
    "has this run produced anything streamable in this process".
    """
    if not events:
        return
    s = _streams.setdefault(run_id, _Stream())
    s.events.extend(events)
    if len(s.events) > _MAX_BUFFER:
        del s.events[: len(s.events) - _MAX_BUFFER]
    for q in list(s.queues):
        q.put_nowait(events)


def close(run_id: str) -> None:
    """End the run's stream: wake every subscriber with the end sentinel and
    drop the buffer. Idempotent."""
    s = _streams.pop(run_id, None)
    if s is None:
        return
    for q in list(s.queues):
        q.put_nowait(None)


def is_live(run_id: str) -> bool:
    return run_id in _streams


async def subscribe(run_id: str) -> AsyncIterator[dict[str, Any]]:
    """Yield the run's events: full replay of the buffer, then live until the
    run closes. Ends immediately if the run isn't live in this process."""
    s = _streams.get(run_id)
    if s is None:
        return
    # Register + snapshot in one synchronous block (no awaits): anything
    # published after this lands in the queue, anything before is in the
    # snapshot — exactly-once either way.
    q: asyncio.Queue[list[dict[str, Any]] | None] = asyncio.Queue()
    s.queues.add(q)
    snapshot = list(s.events)
    try:
        for ev in snapshot:
            yield ev
        while True:
            batch = await q.get()
            if batch is None:
                return
            for ev in batch:
                yield ev
    finally:
        s.queues.discard(q)
