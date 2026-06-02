"""build_options_for wires the SDK options the cookbooks recommend."""

from __future__ import annotations

from bran.agents import build_options_for, get_agent
from bran.config import SETTINGS


def test_max_buffer_size_is_set():
    opts = build_options_for(get_agent("research"))
    assert opts.max_buffer_size == SETTINGS.max_buffer_size
    assert opts.max_buffer_size >= 10 * 1024 * 1024  # well above the SDK's 1MB default


def test_setting_sources_loads_project_settings():
    opts = build_options_for(get_agent("orchestrator"))
    # "project" must be present or filesystem .claude/ (subagents, commands,
    # skills) and CLAUDE.md won't load — the whole bran agent story depends on it.
    assert opts.setting_sources is not None
    assert "project" in opts.setting_sources


def test_default_max_buffer_size_value():
    assert SETTINGS.max_buffer_size == 10 * 1024 * 1024
