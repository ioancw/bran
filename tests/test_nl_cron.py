"""Natural-language → cron parsing. A wrong cron is a *silent* failure (a
briefing that never fires), and the module's contract is echo-and-confirm — so
these assert both the cron AND the human echo, plus the error branches."""

from __future__ import annotations

import pytest

from bran.nl_cron import NlCronParseError, _parse_time, humanize_cron, parse


@pytest.mark.parametrize(
    "text, cron, human",
    [
        ("every weekday at 9am", "0 9 * * mon-fri", "every weekday at 09:00"),
        ("every weekday at 7:30am", "30 7 * * mon-fri", "every weekday at 07:30"),
        ("every weekend at 10am", "0 10 * * sat,sun", "every weekend at 10:00"),
        ("every Monday at 8:30", "30 8 * * mon", "every Monday at 08:30"),
        ("every friday at 6pm", "0 18 * * fri", "every Friday at 18:00"),
        ("daily at 18:30", "30 18 * * *", "every day at 18:30"),
        ("every day at noon", "0 12 * * *", "every day at 12:00"),
        ("daily at midnight", "0 0 * * *", "every day at 00:00"),
        ("every 2 hours", "0 */2 * * *", "every 2 hours"),
        ("every 30 minutes", "*/30 * * * *", "every 30 minutes"),
        ("hourly", "0 * * * *", "every hour on the hour"),
        ("first of every month at 9am", "0 9 1 * *", "first of every month at 09:00"),
        # raw cron passes through, validated + humanized
        ("0 9 * * mon-fri", "0 9 * * mon-fri", "every weekday at 09:00"),
    ],
)
def test_parse_table(text, cron, human):
    r = parse(text)
    assert r.cron == cron
    assert r.human == human


@pytest.mark.parametrize(
    "text, hour, minute",
    [
        ("12am", 0, 0),      # midnight, the classic edge
        ("12pm", 12, 0),     # noon, the other edge
        ("noon", 12, 0),
        ("midnight", 0, 0),
        ("9am", 9, 0),
        ("9pm", 21, 0),
        ("09:30", 9, 30),
        ("11pm", 23, 0),
        ("1am", 1, 0),
    ],
)
def test_parse_time_edges(text, hour, minute):
    assert _parse_time(text) == (hour, minute)


@pytest.mark.parametrize("text", ["", "13am", "25:00", "9:99pm", "gibberish o'clock"])
def test_parse_time_rejects(text):
    assert _parse_time(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "",                       # empty
        "every 90 minutes",       # out of 1–59 range
        "every 40 hours",         # out of 1–23 range
        "sometime next tuesday",  # unrecognised
    ],
)
def test_parse_raises_on_bad_input(text):
    with pytest.raises(NlCronParseError):
        parse(text)


def test_humanize_unknown_cron_falls_back():
    assert humanize_cron("5 4 3 2 1") == "cron `5 4 3 2 1`"
    assert humanize_cron("not a cron") == "not a cron"
