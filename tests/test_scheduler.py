"""Cron parsing for schedule triggers."""

from __future__ import annotations

import uuid

import pytest
from apscheduler.triggers.cron import CronTrigger

from bran.persistence import ProjectRecord, insert_project
from bran.scheduler import _project_append_system, _translate_dow, _trigger_from_cron


def test_valid_five_field_cron():
    trigger = _trigger_from_cron("0 8 * * *")
    assert isinstance(trigger, CronTrigger)


@pytest.mark.parametrize("expr", ["0 8 * *", "0 8 * * * *", "", "daily"])
def test_invalid_field_count_raises(expr):
    with pytest.raises(ValueError):
        _trigger_from_cron(expr)


# Cron numbers weekdays 0/7=Sunday..6=Saturday; APScheduler 0=Monday..6=Sunday.
@pytest.mark.parametrize(
    ("dow", "expected"),
    [
        ("*", "*"),
        ("1", "mon"),
        ("0", "sun"),
        ("7", "sun"),
        ("1-5", "mon,tue,wed,thu,fri"),
        ("0,3", "sun,wed"),
        ("0,7", "sun"),
        ("*/2", "sun,tue,thu,sat"),
        ("1-5/2", "mon,wed,fri"),
        ("mon-fri", "mon-fri"),
        ("sat,sun", "sat,sun"),
    ],
)
def test_translate_dow(dow, expected):
    assert _translate_dow(dow) == expected


@pytest.mark.parametrize("dow", ["8", "1-9", "1/0"])
def test_translate_dow_invalid_raises(dow):
    with pytest.raises(ValueError):
        _translate_dow(dow)


def test_numeric_cron_monday_fires_on_monday():
    """`0 9 * * 1` means Monday in cron; without translation APScheduler
    would read day_of_week=1 as Tuesday."""
    from datetime import datetime

    trigger = _trigger_from_cron("0 9 * * 1")
    # A Wednesday; the next fire must be the following Monday.
    after = datetime(2026, 6, 10, 12, 0, tzinfo=trigger.timezone)
    nxt = trigger.get_next_fire_time(None, after)
    assert nxt.weekday() == 0  # Monday


def test_standalone_runner_has_no_project_memory():
    assert _project_append_system(None) is None


def test_attached_runner_injects_brief_and_memory():
    from bran.persistence import add_project_memory

    p = ProjectRecord.new(name=f"proj-{uuid.uuid4().hex[:6]}", instructions="Cite sources. EU focus.")
    insert_project(p)
    add_project_memory(p.id, "Prefer Bloomberg over Reuters.")
    out = _project_append_system(p.id)
    assert "## Instructions\nCite sources. EU focus." in out
    assert "## Memory\n- Prefer Bloomberg over Reuters." in out


def test_attached_runner_empty_still_describes_files_folder():
    # A project always exposes its files folder as a read+write workspace, so an
    # attached runner with no instructions/memory still gets the Files section
    # (so it can save its output there) but no Instructions/Memory sections.
    p = ProjectRecord.new(name=f"proj-{uuid.uuid4().hex[:6]}", instructions="   ")
    insert_project(p)
    out = _project_append_system(p.id)
    assert out is not None
    assert "## Files" in out
    assert "## Instructions" not in out
    assert "## Memory" not in out
