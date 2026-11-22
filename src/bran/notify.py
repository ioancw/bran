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

What reaches the phone is the user's choice (`rules()`, edited in Settings):
which kinds of event notify, and quiet hours during which everything but an
alert arrives silently. `push()` sends the notifications that are not about a
run finishing: a teammate waiting on an answer, the Claude sign-in running out.

The runner calls `notify_completion(record)` in a `finally` block, so a
notifier failure can't break the run. Failed notifiers are logged and
swallowed.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import os
import re
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

# --- what notifies, and when (app_settings: notify_rules) ---------------------
RULES_KEY = "notify_rules"
# reports: an automation delivered. background: a run you started finished.
# failures: something did not finish. questions: a teammate is waiting on you.
# signin: the Claude sign-in ran out.
RULE_KINDS = ("reports", "background", "failures", "questions", "signin")
_CLOCK = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def rules() -> dict[str, Any]:
    """The user's notification rules; everything on, no quiet hours, until
    they say otherwise."""
    from bran.persistence import get_setting

    out: dict[str, Any] = {k: True for k in RULE_KINDS}
    out.update(quiet_from="", quiet_to="")
    try:
        saved = json.loads(get_setting(RULES_KEY) or "{}")
    except ValueError:
        saved = {}
    if isinstance(saved, dict):
        for k in RULE_KINDS:
            if isinstance(saved.get(k), bool):
                out[k] = saved[k]
        for k in ("quiet_from", "quiet_to"):
            v = saved.get(k)
            if isinstance(v, str) and (v == "" or _CLOCK.fullmatch(v)):
                out[k] = v
    return out


def save_rules(changes: dict[str, Any]) -> dict[str, Any]:
    """Merge `changes` into the stored rules (unknown or malformed entries are
    dropped by `rules()` on the way back out)."""
    from bran.persistence import set_setting

    merged = {**rules(), **{k: v for k, v in changes.items() if k in (*RULE_KINDS, "quiet_from", "quiet_to")}}
    set_setting(RULES_KEY, json.dumps(merged))
    return rules()


def in_quiet_hours(r: dict[str, Any] | None = None, now: Any = None) -> bool:
    """True inside the user's quiet hours (server-local clock; the window may
    run over midnight)."""
    from datetime import datetime

    r = r or rules()
    start, end = r.get("quiet_from") or "", r.get("quiet_to") or ""
    if not start or not end or start == end:
        return False
    t = (now or datetime.now()).strftime("%H:%M")
    return (start <= t < end) if start < end else (t >= start or t < end)


def kind_of(record: RunRecord) -> str | None:
    """Which rule a finished run falls under; None for one nobody needs
    telling about (a run the user cancelled)."""
    if record.status == "cancelled":
        return None
    if record.status != "completed":
        return "failures"
    return "reports" if record.schedule_id else "background"


def _header_text(s: str) -> str:
    """A header value that survives the trip: plain when it is ASCII, RFC 2047
    otherwise (ntfy decodes it; a raw "£" or "—" would not encode at all)."""
    s = " ".join(s.split())
    try:
        s.encode("ascii")
        return s
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode("ascii") + "?="


def _run_name(record: RunRecord) -> str:
    """What the user calls the thing that ran: the automation's name, else
    the agent."""
    if record.schedule_id:
        try:
            from bran.persistence import list_schedules

            for s in list_schedules():
                if s.id == record.schedule_id:
                    return s.name
        except Exception:  # noqa: BLE001 — a name is a nicety
            pass
    return record.agent


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
    # The user's rules: is this kind of event wanted at all, and is it a
    # time to be quiet (an alert is never quiet).
    alert = is_alert(record)
    wanted = rules()
    kind = kind_of(record)
    if kind is None or not wanted[kind]:
        return
    quiet = in_quiet_hours(wanted) and not alert
    # Lazy import keeps httpx off the hot path until a notifier actually fires.
    import httpx

    name = _run_name(record)
    excerpt = ""
    # ntfy.sh-friendly headers; harmless for other targets. Alert-mode runs
    # that crossed their significance bar outrank everything — that's the
    # whole point of a sensing runner.
    if alert:
        headers = {
            "Title": _header_text(f"bran ALERT: {name}"),
            "Priority": "urgent",
            "Tags": "rotating_light",
        }
    elif record.status == "completed":
        # The lock screen shows the report itself: its headline, then how it
        # opens (not "agent completed" and a line of markdown).
        from bran.teammates import delivery_title

        title, excerpt = delivery_title(record, fallback=name)
        headers = {
            "Title": _header_text(title),
            "Priority": "low" if quiet else "default",
            "Tags": "newspaper" if record.schedule_id else "white_check_mark",
        }
    else:
        headers = {
            "Title": _header_text(f"{name} {record.status}"),
            "Priority": "low" if quiet else "high",
            "Tags": "x",
        }
    # Tapping the notification opens the report's reading view — when bran
    # has been told the address it is reachable at from the phone.
    link = read_link(record)
    if link:
        headers["Click"] = link
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if _webhook_format(url) == "ntfy":
                body = _result_snippet(record, limit=600) if alert else (excerpt or _result_snippet(record, limit=600))
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
    link = read_link(record)
    if link:
        payload["url"] = link
    return payload


def app_link(path: str) -> str | None:
    """Where `path` of the app is from the user's phone, when BRAN_PUBLIC_URL
    says where this server can be reached from outside (e.g. a Tailscale
    name). Unset — the default, since bran listens on localhost — there is no
    link to give."""
    base = (os.getenv("BRAN_PUBLIC_URL") or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return None
    return base + path


def read_link(record: RunRecord) -> str | None:
    """The reading-view URL of a run (see `app_link`)."""
    return app_link(f"/app/read/{record.id}")


def _push_request(
    kind: str, title: str, body: str, priority: str, tags: str, click: str | None,
) -> tuple[str, dict[str, Any]] | None:
    """(url, httpx post arguments) for a notification that is not about a run
    finishing, or None when it should not be sent: no target, or the user
    switched this kind off. "test" always goes."""
    url = os.getenv("BRAN_NOTIFY_WEBHOOK_URL")
    if not url:
        return None
    if kind != "test":
        wanted = rules()
        if not wanted.get(kind, True):
            return None
        if in_quiet_hours(wanted):
            priority = "low"
    headers = {"Title": _header_text(title), "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    if _webhook_format(url) == "ntfy":
        return url, {"content": body.encode("utf-8"), "headers": headers}
    payload: dict[str, Any] = {"event": kind, "title": title, "message": body}
    if click:
        payload["url"] = click
    return url, {"json": payload, "headers": headers}


def _delivered(resp: Any, url: str, kind: str) -> bool:
    code = getattr(resp, "status_code", None)
    if isinstance(code, int) and code >= 400:
        log.warning("push (%s): %s answered HTTP %s — not delivered", kind, _safe_target(url), code)
        return False
    return True


async def push(
    kind: str, title: str, body: str, *,
    priority: str = "default", tags: str = "", click: str | None = None,
) -> bool:
    """Send a notification about something other than a finished run (`kind`
    is one of RULE_KINDS, or "test"). True when it was handed over. Never
    raises."""
    req = _push_request(kind, title, body, priority, tags, click)
    if req is None:
        return False
    import httpx

    url, kwargs = req
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            return _delivered(await client.post(url, **kwargs), url, kind)
    except Exception:
        log.exception("push (%s) failed", kind)
        return False


def push_sync(
    kind: str, title: str, body: str, *,
    priority: str = "default", tags: str = "", click: str | None = None,
) -> bool:
    """`push` for code that runs outside the event loop (the scheduler's
    heartbeat thread)."""
    req = _push_request(kind, title, body, priority, tags, click)
    if req is None:
        return False
    import httpx

    url, kwargs = req
    try:
        with httpx.Client(timeout=10) as client:
            return _delivered(client.post(url, **kwargs), url, kind)
    except Exception:
        log.exception("push (%s) failed", kind)
        return False


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
