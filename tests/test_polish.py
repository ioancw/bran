"""Polish-pass behaviours: version single-sourcing, extra_options guard, lazy dirs."""

from __future__ import annotations

import asyncio

import pytest

import bran
from bran.config import SETTINGS
from bran.runner import run_agent


def test_version_resolves_from_metadata():
    assert isinstance(bran.__version__, str)
    assert bran.__version__  # non-empty


def test_extra_options_rejects_unknown_field():
    # The guard must fire before the SDK query runs, so no agent backend needed.
    with pytest.raises(ValueError, match="Unknown ClaudeAgentOptions field"):
        asyncio.run(run_agent("research", "hi", extra_options={"definitely_not_real": 1}))


def test_ensure_dirs_is_idempotent_and_creates_briefings():
    SETTINGS.ensure_dirs()
    SETTINGS.ensure_dirs()  # second call must not raise
    assert SETTINGS.bran_home.is_dir()
    assert SETTINGS.briefings_dir.is_dir()
