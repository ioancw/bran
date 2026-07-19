"""Notification hooks fired when an agent run completes.

A notifier is any callable taking a `RunRecord` — sync or async. Register
your own with `register_notifier(fn)`, or rely on the built-ins that
auto-install based on environment variables:

    BRAN_NOTIFY_WEBHOOK_URL=https://ntfy.sh/your-topic   (push per finished run)
    BRAN_NOTIFY_FORMAT=ntfy|json                         (auto-detected from URL)
    BRAN_NOTIFY_BELL=1                                   (console bell + line)

The webhook skips chat-turn runs (the user is watching those live); it fires
for runner/spawn/manual background work, and escalates alert-mode runs that
crossed their significance bar.

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

# Contract with alert-mode runners (schedules.alert, see scheduler.py): when
# the run's significance bar is crossed, the agent leads its report with this
# marker. Notifiers escalate marked runs; the frontend badges them. Keep in
# sync with the frontend's isAlert helper.
ALERT_MARKER = "🚨 ALERT"

_notifiers: list[Notifier] = []


def is_alert(record: RunRecord) -> bool:
    """True when a completed run's report leads with the alert marker."""
    return (
        record.status == "completed"
        and (record.result or "").lstrip().startswith(ALERT_MARKER)
    )


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


def _webhook_format(url: str) -> str:
    """"ntfy" (plain-text body a phone notification can show) or "json" (full
    record for Slack/Discord/custom endpoints). Auto-detected from the URL
    host; override with BRAN_NOTIFY_FORMAT=ntfy|json (e.g. self-hosted ntfy
    on a domain the auto-detect can't recognise)."""
    fmt = (os.getenv("BRAN_NOTIFY_FORMAT") or "").strip().lower()
    if fmt in ("ntfy", "json"):
        return fmt
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    return "ntfy" if (host == "ntfy.sh" or host.startswith("ntfy.")) else "json"


async def webhook_notifier(record: RunRecord) -> None:
    """Push the run to BRAN_NOTIFY_WEBHOOK_URL.

    ntfy targets get a plain-text body (the result snippet — what you want on
    a lock screen); everything else (Slack/Discord/custom) gets the run record
    as JSON with `summary` and `alert` fields added.
    """
    url = os.getenv("BRAN_NOTIFY_WEBHOOK_URL")
    if not url:
        return
    # Chat turns are a conversation the user is already watching — pushing
    # them to a phone would ping on every message they themselves sent.
    # Background work (runner/spawn/manual) is what notifications are for.
    if record.source == "chat":
        return
    # Completed members of a synthesised fan-out don't push individually —
    # the synthesis run delivers the combined answer as one notification.
    from bran.synthesis import suppresses_notification

    if suppresses_notification(record):
        return
    # Lazy import keeps httpx off the hot path until a notifier actually fires.
    import httpx

    alert = is_alert(record)
    # ntfy.sh-friendly headers; harmless for other targets. Alert-mode runs
    # that crossed their significance bar outrank everything — that's the
    # whole point of a sensing runner.
    if alert:
        headers = {
            "Title": f"bran ALERT: {record.agent}",
            "Priority": "urgent",
            "Tags": "rotating_light",
        }
    else:
        headers = {
            "Title": f"bran: {record.agent} {record.status}",
            "Priority": "default" if record.status == "completed" else "high",
            "Tags": "white_check_mark" if record.status == "completed" else "x",
        }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if _webhook_format(url) == "ntfy":
                body = _result_snippet(record, limit=600)
                if record.status != "completed":
                    body = (record.error or "").strip()[:600]
                body = body or f"{record.agent} {record.status}"
                await client.post(url, content=body.encode("utf-8"), headers=headers)
            else:
                payload = asdict(record)
                payload["summary"] = _result_snippet(record)
                payload["alert"] = alert
                await client.post(url, json=payload, headers=headers)
    except Exception:
        log.exception("webhook notifier failed")


def bell_notifier(record: RunRecord) -> None:
    """Print a console bell + one-line summary to stderr."""
    badge = "🚨" if is_alert(record) else {"completed": "✓", "failed": "✗"}.get(record.status, "?")
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
