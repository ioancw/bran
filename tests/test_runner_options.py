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


def test_user_instructions_layered_into_every_agent_prompt():
    """The global "About me" (Settings) reaches every agent's system prompt."""
    from bran.persistence import set_setting

    set_setting("user_instructions", "I prefer terse bullet-point answers.")
    try:
        for name in ("orchestrator", "research"):
            opts = build_options_for(get_agent(name))
            assert "## About the user" in opts.system_prompt
            assert "terse bullet-point answers" in opts.system_prompt
        # append_system (project context) still lands AFTER the about block.
        opts = build_options_for(get_agent("orchestrator"), append_system="## Project context\nX")
        assert opts.system_prompt.index("## About the user") < opts.system_prompt.index("## Project context")
    finally:
        set_setting("user_instructions", "")
    # Cleared → no empty section left behind.
    opts = build_options_for(get_agent("orchestrator"))
    assert "## About the user" not in opts.system_prompt


def test_settings_kv_roundtrip():
    from bran.persistence import get_setting, set_setting

    assert get_setting("nope", "fallback") == "fallback"
    set_setting("k", "v1")
    set_setting("k", "v2")  # upsert
    assert get_setting("k") == "v2"
