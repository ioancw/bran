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
from typing import Any

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
    a lock screen); everything else (Slack/Discord/custom) gets the run's public
    fields as JSON (see `webhook_payload`) with `summary` and `alert` added.
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
                resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
            else:
                resp = await client.post(
                    url, json=webhook_payload(record, alert), headers=headers
                )
        code = getattr(resp, "status_code", None)
        if isinstance(code, int) and code >= 400:
            log.warning(
                "webhook notifier: %s answered HTTP %s for run %s — the notification was not delivered",
                _safe_target(url), code, record.id,
            )
    except Exception:
        log.exception("webhook notifier failed")


# What a JSON webhook receives. An explicit list, never asdict(record): the
# record's metadata holds internal bookkeeping (and used to hold the whole
# stored transcript — tool results, file contents, fetched pages), none of
# which belongs in a third party's channel.
_WEBHOOK_FIELDS = (
    "id", "agent", "task", "status", "source", "project_id", "schedule_id",
    "session_id", "parent_run_id", "started_at", "ended_at", "duration_ms",
    "total_cost_usd", "num_turns", "result", "error",
)
_WEBHOOK_TEXT_CAP = 32_000


def webhook_payload(record: RunRecord, alert: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name in _WEBHOOK_FIELDS:
        value = getattr(record, name)
        if isinstance(value, str) and len(value) > _WEBHOOK_TEXT_CAP:
            value = value[:_WEBHOOK_TEXT_CAP] + "\n[… truncated …]"
        payload[name] = value
    payload["summary"] = _result_snippet(record)
    payload["alert"] = alert
    return payload


def _safe_target(url: str) -> str:
    """The webhook's host for log lines — the path is often the secret."""
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or "webhook"


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
