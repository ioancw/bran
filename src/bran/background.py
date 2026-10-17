"""Strong references for fire-and-forget asyncio tasks.

The event loop only keeps a *weak* reference to tasks created via
`asyncio.create_task`, so a background task with no other strong reference can
be garbage-collected mid-run — it then vanishes silently, leaving e.g. an
agent run stuck in `running` forever with no traceback. We hold a strong
reference in a module-level set until the task completes, then drop it.

See the warning under
https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Coroutine
from typing import Any

# Strong references to in-flight background tasks. The done-callback removes
# each task once it finishes, so this set only ever holds live work.
_background_tasks: set[asyncio.Task[Any]] = set()

# Ambient context for the currently-executing run, set by runner._drive. Tools
# like spawn_agent read these so a background run fired mid-conversation inherits
# the originating chat's project and links back to its parent run. Defaults mean
# "standalone / no parent" when nothing is running.
current_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bran_current_project_id", default=None
)
current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bran_current_run_id", default=None
)


# ---------------------------------------------------------------------------
# Run slots: a semaphore that bounds how many background runs execute at once.
# asyncio primitives bind to the loop they're first used on; tests spin up a
# fresh loop per asyncio.run(), so the primitives are rebuilt when the loop
# changes (nothing can be held across loops anyway).
# ---------------------------------------------------------------------------
_slots: asyncio.Semaphore | None = None
_slots_loop: asyncio.AbstractEventLoop | None = None
_session_locks: dict[str, asyncio.Lock] = {}
_session_locks_loop: asyncio.AbstractEventLoop | None = None


def run_slots() -> asyncio.Semaphore:
    """The process-wide concurrency gate for background runs (see
    SETTINGS.max_concurrent_runs). Acquire it around the whole run."""
    global _slots, _slots_loop
    from bran.config import SETTINGS

    loop = asyncio.get_running_loop()
    if _slots is None or _slots_loop is not loop:
        _slots = asyncio.Semaphore(SETTINGS.max_concurrent_runs)
        _slots_loop = loop
    return _slots


def slots_in_use() -> int:
    """How many background runs currently hold a slot (0 when idle)."""
    from bran.config import SETTINGS

    if _slots is None:
        return 0
    return SETTINGS.max_concurrent_runs - _slots._value  # noqa: SLF001 — informational only


def session_lock(session_id: str) -> asyncio.Lock:
    """One lock per SDK session. Everything that *resumes* a session (a chat
    turn, the headless synthesis turn) must hold it: two CLI processes
    appending to the same transcript would interleave the conversation."""
    global _session_locks_loop
    loop = asyncio.get_running_loop()
    if _session_locks_loop is not loop:
        _session_locks.clear()
        _session_locks_loop = loop
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = _session_locks[session_id] = asyncio.Lock()
    return lock


def spawn_background(
    coro: Coroutine[Any, Any, Any], *, name: str | None = None
) -> asyncio.Task[Any]:
    """Schedule `coro` on the running loop and keep it alive until done.

    Drop-in replacement for `asyncio.create_task` for fire-and-forget work
    whose result nobody awaits.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def cancel_background(run_id: str) -> int:
    """Cancel any in-flight background task(s) for `run_id`. Returns the count.

    Call sites name their tasks `"<prefix>:<run_id>"` (e.g. "spawn:<id>",
    "ui-run:<id>"), so we match on the `:<run_id>` suffix. Cancellation raises
    CancelledError inside the run loop, which the runner records as a terminal
    "cancelled" status (see runner._drive) before the SDK subprocess is torn
    down. No-op if the run isn't a live background task on this loop.
    """
    cancelled = 0
    for task in list(_background_tasks):
        if task.done():
            continue
        if (task.get_name() or "").endswith(f":{run_id}"):
            task.cancel()
            cancelled += 1
    return cancelled
