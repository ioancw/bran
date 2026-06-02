"""@-mention agent routing used by the web chat surface."""

from __future__ import annotations

from bran.web.spa_api import _parse_agent_prefix


def test_plain_prompt_routes_to_orchestrator():
    assert _parse_agent_prefix("what is MCP?") == ("orchestrator", "what is MCP?")


def test_known_agent_mention_is_stripped():
    assert _parse_agent_prefix("@research find me sources") == (
        "research",
        "find me sources",
    )


def test_agent_dash_prefix_is_accepted():
    # The UI renders subagents as `@agent-research`; both forms resolve.
    assert _parse_agent_prefix("@agent-research dig in") == ("research", "dig in")


def test_unknown_mention_falls_back_to_orchestrator_verbatim():
    assert _parse_agent_prefix("@nobody hello") == ("orchestrator", "@nobody hello")


def test_multiline_prompt_after_mention_is_preserved():
    agent, rest = _parse_agent_prefix("@summariser line one\nline two")
    assert agent == "summariser"
    assert rest == "line one\nline two"
