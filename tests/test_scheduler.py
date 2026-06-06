"""Cron parsing for schedule triggers."""

from __future__ import annotations

import uuid

import pytest
from apscheduler.triggers.cron import CronTrigger

from bran.persistence import ProjectRecord, insert_project, update_project
from bran.scheduler import _project_append_system, _trigger_from_cron


def test_valid_five_field_cron():
    trigger = _trigger_from_cron("0 8 * * *")
    assert isinstance(trigger, CronTrigger)


@pytest.mark.parametrize("expr", ["0 8 * *", "0 8 * * * *", "", "daily"])
def test_invalid_field_count_raises(expr):
    with pytest.raises(ValueError):
        _trigger_from_cron(expr)


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


def test_attached_runner_with_empty_memory_is_none():
    p = ProjectRecord.new(name=f"proj-{uuid.uuid4().hex[:6]}", instructions="   ")
    insert_project(p)
    assert _project_append_system(p.id) is None
