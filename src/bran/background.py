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
from typing import Any, Coroutine

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
