"""Natural-language → cron for conversational runner scheduling.

Lets `create_runner` accept free text like *"every weekday at 9am"* (not only a
raw 5-field cron or an ISO datetime) and ALWAYS echo a human-readable
description back so the user can confirm the interpretation — a drifting cron is
a silent failure, so echo-and-confirm is the contract.

Adapted from light_cc's `nl_cron`, with one bran-specific fix: weekday fields
are emitted as APScheduler-friendly NAMES (``mon-fri``, ``sat,sun``, ``mon``)
rather than numbers, because APScheduler's CronTrigger treats ``0`` as Monday
while standard cron treats ``0`` as Sunday — using names sidesteps the mismatch.

Scope is deliberately thin: cover the patterns people actually want, and tell
them clearly (with a recovery path) when something isn't recognised, rather than
guessing wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class NlCronParseError(ValueError):
    """Raised when natural-language input can't be parsed into a cron."""


@dataclass(frozen=True)
class ParseResult:
    cron: str
    human: str  # short description to echo back to the user


# word -> APScheduler day_of_week name
_WEEKDAY_NAMES = {
    "monday": "mon", "mon": "mon",
    "tuesday": "tue", "tue": "tue", "tues": "tue",
    "wednesday": "wed", "wed": "wed",
    "thursday": "thu", "thu": "thu", "thur": "thu", "thurs": "thu",
    "friday": "fri", "fri": "fri",
    "saturday": "sat", "sat": "sat",
    "sunday": "sun", "sun": "sun",
}
_LABELS = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}

# Match "9am", "9 am", "9:30am", "09:30", "9", "9pm", "noon", "midnight".
_TIME_RE = re.compile(
    r"(?:(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?)",
    re.IGNORECASE,
)


def _parse_time(text: str) -> tuple[int, int] | None:
    """Parse 'HH', 'HH:MM', 'HHam', 'HHpm', 'noon', 'midnight' → (hour, minute)."""
    s = text.strip().lower()
    if not s:
        return None
    if s == "noon":
        return (12, 0)
    if s == "midnight":
        return (0, 0)
    m = _TIME_RE.fullmatch(s)
    if not m:
        return None
    hour = int(m.group("hour"))
    minute = int(m.group("minute") or 0)
    ampm = m.group("ampm")
    if ampm == "am":
        if hour == 12:
            hour = 0
        elif not (0 <= hour <= 12):
            return None
    elif ampm == "pm":
        if hour == 12:
            pass
        elif 1 <= hour <= 11:
            hour += 12
        else:
            return None
    else:
        if not (0 <= hour <= 23):
            return None
    if not (0 <= minute <= 59):
        return None
    return (hour, minute)


def _hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def _valid_cron(s: str) -> bool:
    """True if `s` is a 5-field cron expression APScheduler accepts."""
    parts = s.strip().split()
    if len(parts) != 5:
        return False
    try:
        from apscheduler.triggers.cron import CronTrigger

        minute, hour, dom, month, dow = parts
        CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow)
        return True
    except Exception:
        return False


def humanize_cron(cron: str) -> str:
    """Best-effort short description of a 5-field cron for echoing back. Falls
    back to ``cron `...``` so the user still sees what was stored."""
    parts = cron.strip().split()
    if len(parts) != 5:
        return cron
    minute, hour, dom, month, dow = parts

    def at(h: str, m: str) -> str:
        return f"{int(h):02d}:{int(m):02d}"

    if minute.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return f"every {minute[2:]} minutes"
    if minute == "0" and hour.startswith("*/") and dom == "*" and month == "*" and dow == "*":
        return f"every {hour[2:]} hours"
    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "every hour on the hour"
    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*":
        if dow == "*":
            return f"every day at {at(hour, minute)}"
        if dow == "mon-fri":
            return f"every weekday at {at(hour, minute)}"
        if dow in ("sat,sun", "sat-sun"):
            return f"every weekend at {at(hour, minute)}"
        if dow in _LABELS:
            return f"every {_LABELS[dow]} at {at(hour, minute)}"
    if minute.isdigit() and hour.isdigit() and dom == "1" and month == "*" and dow == "*":
        return f"first of every month at {at(hour, minute)}"
    return f"cron `{cron}`"


def parse(text: str) -> ParseResult:
    """Parse a schedule string into ``(cron, human)``.

    Accepts a raw 5-field cron (passed through, validated + humanized) or a set
    of natural-language patterns. Raises ``NlCronParseError`` with a specific,
    recoverable suggestion when the input isn't recognised.
    """
    raw = text.strip()
    if not raw:
        raise NlCronParseError(
            "Please describe when this should run — e.g. `every weekday at 9am`, "
            "`every Monday at 8:30`, or a 5-field cron like `0 9 * * mon-fri`."
        )

    if _valid_cron(raw):
        return ParseResult(cron=raw, human=humanize_cron(raw))

    s = re.sub(r"\s+", " ", raw.lower()).strip()
    if s.startswith("at "):
        s = s[3:].strip()

    # every N minutes
    m = re.fullmatch(r"every (\d{1,3}) ?min(?:ute)?s?", s)
    if m:
        n = int(m.group(1))
        if not (1 <= n <= 59):
            raise NlCronParseError(f"`every {n} minutes` isn't supported — must be 1–59.")
        return ParseResult(cron=f"*/{n} * * * *", human=f"every {n} minutes")

    # every N hours
    m = re.fullmatch(r"every (\d{1,2}) ?h(?:our|r)?s?", s)
    if m:
        n = int(m.group(1))
        if not (1 <= n <= 23):
            raise NlCronParseError(f"`every {n} hours` isn't supported — must be 1–23.")
        return ParseResult(cron=f"0 */{n} * * *", human=f"every {n} hours")

    # every hour / hourly
    if s in ("every hour", "hourly", "every hour on the hour"):
        return ParseResult(cron="0 * * * *", human="every hour on the hour")

    # every weekday/weekend at <time>
    m = re.fullmatch(r"every (weekday|weekdays|weekend|weekends)(?: at (.+))?", s)
    if m:
        which = m.group(1)
        t = _parse_time((m.group(2) or "9am").strip())
        if t is None:
            raise NlCronParseError("Couldn't parse the time. Try `9am`, `09:00`, or `9:30pm`.")
        h, mn = t
        if which.startswith("weekday"):
            return ParseResult(cron=f"{mn} {h} * * mon-fri", human=f"every weekday at {_hhmm(h, mn)}")
        return ParseResult(cron=f"{mn} {h} * * sat,sun", human=f"every weekend at {_hhmm(h, mn)}")

    # every <weekday> [morning/afternoon/...] at <time>
    m = re.fullmatch(r"every (\w+)(?:s)?(?: (?:morning|afternoon|evening|night))?(?: at (.+))?", s)
    if m and m.group(1) in _WEEKDAY_NAMES:
        time_str = (m.group(2) or "").strip()
        if not time_str:
            if " afternoon" in s:
                t: tuple[int, int] = (14, 0)
            elif " evening" in s:
                t = (18, 0)
            elif " night" in s:
                t = (21, 0)
            else:
                t = (9, 0)
        else:
            parsed = _parse_time(time_str)
            if parsed is None:
                raise NlCronParseError("Couldn't parse the time. Try `9am`, `09:00`, or `9:30pm`.")
            t = parsed
        h, mn = t
        dow = _WEEKDAY_NAMES[m.group(1)]
        return ParseResult(cron=f"{mn} {h} * * {dow}", human=f"every {_LABELS[dow]} at {_hhmm(h, mn)}")

    # daily / every day at <time>
    m = re.fullmatch(r"(?:daily|every day)(?: at (.+))?", s)
    if m:
        t = _parse_time((m.group(1) or "9am").strip())
        if t is None:
            raise NlCronParseError("Couldn't parse the time. Try `9am`, `09:00`, or `9:30pm`.")
        h, mn = t
        return ParseResult(cron=f"{mn} {h} * * *", human=f"every day at {_hhmm(h, mn)}")

    # first of every month at <time>
    m = re.fullmatch(r"(?:first|1st) of (?:every|the) month(?: at (.+))?", s)
    if m:
        t = _parse_time((m.group(1) or "9am").strip())
        if t is None:
            raise NlCronParseError("Couldn't parse the time. Try `9am`, `09:00`, or `9:30pm`.")
        h, mn = t
        return ParseResult(cron=f"{mn} {h} 1 * *", human=f"first of every month at {_hhmm(h, mn)}")

    raise NlCronParseError(
        f"I can't parse `{raw}` yet. Try one of:\n"
        "- `every weekday at 9am`\n"
        "- `every Monday at 8:30`\n"
        "- `daily at 18:30`\n"
        "- `every 2 hours`  ·  `every 30 minutes`\n"
        "- `first of every month at 9am`\n"
        "- or a 5-field cron like `0 9 * * mon-fri`."
    )
