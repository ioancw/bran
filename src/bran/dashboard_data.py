"""Aggregations used by the dashboard hero.

Pulled out of routes.py so the math is unit-testable and the route stays a
thin presentation layer. Everything in here is read-only against the
persistence layer and the schedule registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from bran.config import SETTINGS
from bran.persistence import list_runs, list_schedules


@dataclass
class AgentCount:
    name: str
    count: int


@dataclass
class TodayStats:
    """Numbers shown in the dashboard hero. All scoped to UTC 'today'."""

    runs_completed: int
    runs_failed: int
    runs_running: int
    total_cost_usd: float
    per_agent: list[AgentCount]  # sorted desc by count


@dataclass
class UpcomingSchedule:
    """A schedule and its next fire time (or None if cron is invalid)."""

    name: str
    agent: str
    cron: str
    next_run: datetime | None  # tz-aware UTC


def today_stats(now: datetime | None = None) -> TodayStats:
    """Aggregate today's runs (UTC day boundary)."""
    now = now or datetime.now(timezone.utc)
    today_iso = now.date().isoformat()  # 'YYYY-MM-DD'

    todays = [
        r for r in list_runs(limit=500)
        if r.started_at.startswith(today_iso)
    ]

    completed = sum(1 for r in todays if r.status == "completed")
    failed = sum(1 for r in todays if r.status == "failed")
    running = sum(1 for r in todays if r.status in ("running", "pending"))
    cost = sum((r.total_cost_usd or 0.0) for r in todays)

    by_agent: dict[str, int] = {}
    for r in todays:
        by_agent[r.agent] = by_agent.get(r.agent, 0) + 1

    per_agent = [
        AgentCount(name=name, count=n)
        for name, n in sorted(by_agent.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    return TodayStats(
        runs_completed=completed,
        runs_failed=failed,
        runs_running=running,
        total_cost_usd=cost,
        per_agent=per_agent,
    )


def upcoming_schedules(now: datetime | None = None, limit: int = 5) -> list[UpcomingSchedule]:
    """Return enabled schedules sorted by next fire time (earliest first)."""
    # Lazy-import APScheduler's CronTrigger so this module loads cheaply when
    # the scheduler subsystem isn't being used.
    from apscheduler.triggers.cron import CronTrigger

    now = now or datetime.now(timezone.utc)
    out: list[UpcomingSchedule] = []
    for rec in list_schedules():
        if not rec.enabled:
            continue
        try:
            parts = rec.cron.split()
            if len(parts) != 5:
                raise ValueError("expected 5-field cron")
            trigger = CronTrigger(
                minute=parts[0], hour=parts[1],
                day=parts[2], month=parts[3], day_of_week=parts[4],
            )
            next_fire = trigger.get_next_fire_time(None, now)
        except Exception:
            next_fire = None
        out.append(UpcomingSchedule(
            name=rec.name, agent=rec.agent, cron=rec.cron, next_run=next_fire,
        ))
    # None-next-runs sink to the end; otherwise sort by earliest.
    out.sort(key=lambda u: (u.next_run is None, u.next_run or datetime.max.replace(tzinfo=timezone.utc)))
    return out[:limit]


# ---------------------------------------------------------------------------
# Outputs — agent results grouped by time, the editorial heart of the dashboard
# ---------------------------------------------------------------------------

@dataclass
class OutputItem:
    """One thing an agent produced. Either a briefing (file on disk, rich card)
    or an answer (a completed run's `result`, quieter card)."""
    kind: str           # "briefing" | "answer"
    title: str
    snippet: str
    agent: str
    timestamp: datetime
    time_label: str     # short relative label rendered next to the title
    href: str


@dataclass
class OutputBucket:
    """A time-bucket of outputs. `label` is the eyebrow header (e.g. 'today')."""
    label: str
    items: list[OutputItem] = field(default_factory=list)


def _prose_snippet(text: str, limit: int = 220) -> str:
    """First few lines of real prose, skipping markdown chrome — used to
    generate the snippet shown on output cards."""
    parts: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "*", "|", "```", ">")):
            continue
        parts.append(line)
        if sum(len(p) for p in parts) > limit + 20:
            break
    s = " ".join(parts)
    if len(s) > limit:
        s = s[: limit - 3].rsplit(" ", 1)[0] + "…"
    return s


def _time_label(ts: datetime, now: datetime) -> str:
    """Short relative time for the card corner. 'today' items just show HH:MM."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    today = now.date()
    d = ts.date()
    if d == today:
        return ts.strftime("%H:%M")
    if d == today - timedelta(days=1):
        return f"yesterday {ts.strftime('%H:%M')}"
    if (now - ts).days < 7:
        return ts.strftime("%a %H:%M").lower()
    return ts.strftime("%b %d").lower()


def _bucket_label(d: date, today: date) -> str:
    """Map a date to its display bucket. Auto-grouping per user's pick."""
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())          # Monday
    last_week_start = week_start - timedelta(days=7)
    if d == today:
        return "today"
    if d == yesterday:
        return "yesterday"
    if week_start <= d < yesterday:
        return "this week"
    if last_week_start <= d < week_start:
        return "last week"
    return d.strftime("%B %Y").lower()


def list_outputs(limit: int = 60, now: datetime | None = None) -> list[OutputBucket]:
    """Return briefing files bucketed by time, newest first.

    Only briefing files are surfaced here — chat-style answers from the
    orchestrator are intentionally excluded, since they're already visible on
    /runs and would dilute the editorial feel of the dashboard.
    """
    now = now or datetime.now(timezone.utc)

    items: list[OutputItem] = []
    if SETTINGS.briefings_dir.exists():
        for p in SETTINGS.briefings_dir.glob("*.md"):
            try:
                mtime = p.stat().st_mtime
                body = p.read_text(encoding="utf-8")
            except OSError:
                continue
            ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
            title = p.name.replace(".md", "").replace("briefing_", "").replace("_", " ")
            items.append(OutputItem(
                kind="briefing",
                title=title,
                snippet=_prose_snippet(body, 220),
                agent="finance_news",
                timestamp=ts,
                time_label=_time_label(ts, now),
                href=f"/briefings/{p.name}",
            ))

    items.sort(key=lambda i: i.timestamp, reverse=True)
    items = items[:limit]

    # Bucket — items already sorted, so bucket labels stay monotonic
    buckets: list[OutputBucket] = []
    today = now.date()
    current_label: str | None = None
    for item in items:
        label = _bucket_label(item.timestamp.date(), today)
        if label != current_label:
            buckets.append(OutputBucket(label=label))
            current_label = label
        buckets[-1].items.append(item)
    return buckets


def format_countdown(target: datetime, now: datetime | None = None) -> str:
    """Human-readable 'in 14h 23m' style countdown to a future datetime.

    Returns 'overdue' if target is in the past, 'now' if within a minute.
    """
    now = now or datetime.now(timezone.utc)
    delta = target - now
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "overdue"
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"in {seconds // 60}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"in {h}h {m}m"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"in {d}d {h}h"
