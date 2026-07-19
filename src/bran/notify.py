"""Notification hooks fired when an agent run completes.

A notifier is any callable taking a `RunRecord` — sync or async. Register
your own with `register_notifier(fn)`, or rely on the built-ins that
auto-install based on environment variables:

    BRAN_NOTIFY_WEBHOOK_URL=https://ntfy.sh/your-topic   (POST run JSON)
    BRAN_NOTIFY_BELL=1                                   (console bell + line)

The runner calls `notify_completion(record)` in a `finally` block, so a
notifier failure can't break the run. Failed notifiers are logged and
swallowed.
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from bran.persistence import RunRecord

log = logging.getLogger("bran.notify")

Notifier = Callable[[RunRecord], None | Awaitable[None]]

_notifiers: list[Notifier] = []


def _result_snippet(record: RunRecord, limit: int = 400) -> str:
    """A short, single-paragraph taste of a run's output for notifications.

    Notifications used to carry only status ("agent X completed") — useless for
    actually consuming the result. This pulls the head of the result text so the
    ping delivers the value, not just the fact that something finished.
    """
    text = (record.result or "").strip()
    if not text:
        return ""
    # Collapse to the first non-empty lines up to the limit.
    out = text[:limit].strip()
    if len(text) > limit:
        out += "…"
    return out


def register_notifier(fn: Notifier) -> None:
    """Add a notifier. Safe to call multiple times with the same fn — dedup'd."""
    if fn not in _notifiers:
        _notifiers.append(fn)


def clear_notifiers() -> None:
    """Mostly for tests."""
    _notifiers.clear()


async def notify_completion(record: RunRecord) -> None:
    """Fire every registered notifier. Failures are logged but never raised."""
    for fn in list(_notifiers):
        try:
            result = fn(record)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("notifier %r failed for run %s", fn, record.id)


# ---------------------------------------------------------------------------
# Built-in notifiers
# ---------------------------------------------------------------------------


async def webhook_notifier(record: RunRecord) -> None:
    """POST the run record as JSON to BRAN_NOTIFY_WEBHOOK_URL.

    Works with ntfy.sh, Slack incoming webhooks, Discord webhooks (with a tiny
    payload tweak), or any custom endpoint.
    """
    url = os.getenv("BRAN_NOTIFY_WEBHOOK_URL")
    if not url:
        return
    # Lazy import keeps httpx off the hot path until a notifier actually fires.
    import httpx

    payload = asdict(record)
    # A human-readable taste of the output so consumers (ntfy/Slack/Discord) can
    # show the actual result, not just status. The full text stays in `result`.
    payload["summary"] = _result_snippet(record)
    # ntfy.sh-friendly headers; harmless for other targets.
    headers = {
        "Title": f"bran: {record.agent} {record.status}",
        "Priority": "default" if record.status == "completed" else "high",
        "Tags": "white_check_mark" if record.status == "completed" else "x",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload, headers=headers)
    except Exception:
        log.exception("webhook notifier failed")


def bell_notifier(record: RunRecord) -> None:
    """Print a console bell + one-line summary to stderr."""
    badge = {"completed": "✓", "failed": "✗"}.get(record.status, "?")
    cost = f"${record.total_cost_usd:.4f}" if record.total_cost_usd else "-"
    sys.stderr.write(
        f"\a[bran] {badge} {record.agent} · {record.status} · "
        f"{record.num_turns or 0} turns · {cost} · run {record.id[:8]}\n"
    )
    # A one-line taste of the output so the console ping actually delivers
    # something, not just status. (Single line — keep the bell terse.)
    snippet = _result_snippet(record, limit=160).replace("\n", " ")
    if snippet:
        sys.stderr.write(f"       {snippet}\n")
    sys.stderr.flush()


def install_default_notifiers() -> None:
    """Register built-in notifiers based on environment variables.

    Idempotent — safe to call from every entry point.
    """
    if os.getenv("BRAN_NOTIFY_WEBHOOK_URL"):
        register_notifier(webhook_notifier)
    if os.getenv("BRAN_NOTIFY_BELL", "0") in {"1", "true", "yes"}:
        register_notifier(bell_notifier)
