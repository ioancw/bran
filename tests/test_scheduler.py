"""Cron parsing for schedule triggers."""

from __future__ import annotations

import pytest
from apscheduler.triggers.cron import CronTrigger

from bran.scheduler import _trigger_from_cron


def test_valid_five_field_cron():
    trigger = _trigger_from_cron("0 8 * * *")
    assert isinstance(trigger, CronTrigger)


@pytest.mark.parametrize("expr", ["0 8 * *", "0 8 * * * *", "", "daily"])
def test_invalid_field_count_raises(expr):
    with pytest.raises(ValueError):
        _trigger_from_cron(expr)
