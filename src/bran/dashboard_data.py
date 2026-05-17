"""Aggregations used by the dashboard hero.

Pulled out of routes.py so the math is unit-testable and the route stays a
thin presentation layer. Everything in here is read-only against the
persistence layer and the schedule registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from bran.config import SETTINGS
from bran.persistence import RunRecord, ScheduleRecord, list_runs, list_schedules


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


@dataclass
class LatestBriefing:
    name: str           # filename
    mtime: float        # unix timestamp
    body: str           # full markdown body (small enough to inline)
    snippet: str        # short plain-text preview for dashboard cards


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


def latest_briefing() -> LatestBriefing | None:
    """Return the most recently modified briefing markdown, or None."""
    if not SETTINGS.briefings_dir.exists():
        return None
    files = sorted(
        SETTINGS.briefings_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return None
    p = files[0]
    try:
        body = p.read_text(encoding="utf-8")
    except OSError:
        return None

    # Cheap snippet generator: skip blank lines, headers, list bullets, and
    # markdown link/bold syntax to get the first real prose-y line. Stops at
    # 220 chars. This is template-side ugliness moved to Python where it belongs.
    snippet_parts: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "*", "|", "```", ">")):
            continue
        snippet_parts.append(line)
        if sum(len(p) for p in snippet_parts) > 240:
            break
    snippet = " ".join(snippet_parts)
    if len(snippet) > 220:
        snippet = snippet[:217].rsplit(" ", 1)[0] + "…"

    return LatestBriefing(
        name=p.name, mtime=p.stat().st_mtime, body=body, snippet=snippet,
    )


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
