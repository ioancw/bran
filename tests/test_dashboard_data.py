"""Unit tests for the dashboard's pure aggregation/formatting helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bran.dashboard_data import (
    _bucket_label,
    _prose_snippet,
    _time_label,
    format_countdown,
)

# A fixed "now" so relative-time logic is deterministic. Wednesday.
NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_format_countdown_buckets():
    assert format_countdown(NOW - timedelta(minutes=5), NOW) == "overdue"
    assert format_countdown(NOW + timedelta(seconds=30), NOW) == "now"
    assert format_countdown(NOW + timedelta(minutes=42), NOW) == "in 42m"
    assert format_countdown(NOW + timedelta(hours=3, minutes=15), NOW) == "in 3h 15m"
    assert format_countdown(NOW + timedelta(days=2, hours=4), NOW) == "in 2d 4h"


def test_time_label():
    assert _time_label(NOW, NOW) == "12:00"
    assert _time_label(NOW - timedelta(days=1), NOW) == "yesterday 12:00"
    # Within the week but >1 day ago → weekday abbrev, lowercased.
    assert _time_label(NOW - timedelta(days=2), NOW) == "mon 12:00"
    # Older than a week → month/day.
    assert _time_label(NOW - timedelta(days=30), NOW) == "apr 27"


def test_time_label_assumes_utc_for_naive_input():
    naive = NOW.replace(tzinfo=None)
    assert _time_label(naive, NOW) == "12:00"


def test_bucket_label():
    today = NOW.date()
    assert _bucket_label(today, today) == "today"
    assert _bucket_label(today - timedelta(days=1), today) == "yesterday"
    # Wednesday → Monday/Tuesday of the same week are "this week".
    assert _bucket_label(today - timedelta(days=2), today) == "this week"
    # Something well in the past → "Month YYYY" lowercased.
    assert _bucket_label(today - timedelta(days=90), today) == "february 2026"


def test_prose_snippet_skips_markdown_chrome():
    text = (
        "# Heading\n"
        "- bullet one\n"
        "> a quote\n"
        "Real prose sentence that should survive.\n"
        "```\ncode\n```\n"
        "Another prose line.\n"
    )
    snippet = _prose_snippet(text, limit=220)
    assert "Heading" not in snippet
    assert "bullet" not in snippet
    assert "Real prose sentence that should survive." in snippet


def test_prose_snippet_truncates():
    text = "word " * 200
    snippet = _prose_snippet(text, limit=50)
    assert len(snippet) <= 51  # limit plus the ellipsis char
    assert snippet.endswith("…")
