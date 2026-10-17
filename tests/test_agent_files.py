"""Agents as files: the frontmatter parser, the shipped agents directory, and
the single-spec → (Agent, AgentDefinition) derivation."""

from __future__ import annotations

from pathlib import Path

import pytest

from bran.agent_files import AgentFileError, load_agent_dir, parse_frontmatter, spec_from_text
from bran.agents import (
    AGENTS_DIR,
    TAVILY_TOOLS,
    get_agent,
    list_agents,
    load_agents,
    resolve_tools,
    servers_for,
)
from bran.config import SETTINGS

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parse_frontmatter_forms():
    meta, body = parse_frontmatter(
        "---\n"
        "name: x\n"
        "description: Does things: well\n"
        "tools: [Read, Write]\n"
        "extra:\n"
        "  - one\n"
        "  - two\n"
        "max_turns: 12\n"
        "flag: true\n"
        "quoted: \"a b\"\n"
        "---\n"
        "Body line 1\n\nBody line 2\n"
    )
    assert meta == {
        "name": "x", "description": "Does things: well", "tools": ["Read", "Write"],
        "extra": ["one", "two"], "max_turns": 12, "flag": True, "quoted": "a b",
    }
    assert body == "Body line 1\n\nBody line 2"


def test_claude_code_style_comma_tools_are_accepted():
    spec = spec_from_text("---\ndescription: d\ntools: WebSearch, Read, Grep\n---\nprompt", default_name="t")
    assert spec.tools == ["WebSearch", "Read", "Grep"]
    assert spec.name == "t" and spec.permission_mode == "dontAsk"


@pytest.mark.parametrize("text,msg", [
    ("no frontmatter here", "missing frontmatter"),
    ("---\nname: a\n---\nbody", "description"),
    ("---\ndescription: d\n---\n", "empty"),
    ("---\ndescription: d\nmax_turns: lots\n---\nbody", "max_turns"),
    ("---\ndescription: d\nname: bad name!\n---\nbody", "invalid agent name"),
])
def test_malformed_files_raise(text, msg):
    with pytest.raises(AgentFileError, match=msg):
        spec_from_text(text, default_name="x")


def test_load_dir_names_the_file_and_rejects_duplicates(tmp_path: Path):
    (tmp_path / "a.md").write_text("---\ndescription: d\n---\nbody", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\nname: a\ndescription: d\n---\nbody", encoding="utf-8")
    with pytest.raises(AgentFileError, match="b.md: duplicate agent name 'a'"):
        load_agent_dir(tmp_path)
    (tmp_path / "b.md").write_text("broken", encoding="utf-8")
    with pytest.raises(AgentFileError, match="b.md: missing frontmatter"):
        load_agent_dir(tmp_path)
    assert load_agent_dir(tmp_path / "missing") == []


# ---------------------------------------------------------------------------
# The shipped agents directory
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reload_after(monkeypatch):
    yield
    load_agents()  # leave the registry as the shipped files define it


def test_shipped_agents_load():
    names = {a.name for a in list_agents()}
    assert {"orchestrator", "research", "research-deep", "summariser", "finance-news"} <= names
    for a in list_agents():
        assert a.source and a.source.startswith("agents/") and a.source.endswith(".md")
        assert a.max_turns and a.max_turns > 0
        assert a.system_prompt.rstrip().endswith("parsed as math.")  # math note appended


def test_orchestrator_subagents_derive_from_the_same_files():
    o = get_agent("orchestrator")
    assert set(o.subagents) == {"research", "research-deep", "summariser", "finance-news"}
    assert "Agent" in o.tools  # auto-granted: it has subagents
    r = get_agent("research")
    sub = o.subagents["research"]
    assert sub.prompt == r.system_prompt
    assert sub.tools == r.tools
    assert sub.model == r.model == "sonnet"
    assert sub.maxTurns == r.max_turns == 40
    assert "bran_docs" in sub.mcpServers
    # The roster is rendered from the subagents' own descriptions.
    assert "- `research`:" in o.system_prompt and "- `finance-news`:" in o.system_prompt
    assert "{{roster}}" not in o.system_prompt
    assert o.model == SETTINGS.default_model


def test_templates_and_servers():
    f = get_agent("finance-news")
    assert str(SETTINGS.briefings_dir) in f.system_prompt and "{{briefings_dir}}" not in f.system_prompt
    assert set(f.mcp_servers) == {"bran_docs"} or set(f.mcp_servers) == {"bran_docs", "tavily"}
    o = get_agent("orchestrator")
    assert {"bran", "bran_docs"} <= set(o.mcp_servers)
    assert servers_for(["Read"]) == {}


def test_tavily_tools_follow_the_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert resolve_tools(["Read", "mcp__tavily__*", "mcp__tavily__tavily_search"]) == ["Read"]
    load_agents()
    assert not any(t.startswith("mcp__tavily__") for t in get_agent("research").tools)
    assert "tavily" not in get_agent("research").mcp_servers

    monkeypatch.setenv("TAVILY_API_KEY", "k")
    assert resolve_tools(["Read", "mcp__tavily__*"]) == ["Read", *TAVILY_TOOLS]
    load_agents()
    assert set(TAVILY_TOOLS) <= set(get_agent("research").tools)
    assert get_agent("research").mcp_servers["tavily"]["env"]["TAVILY_API_KEY"] == "k"
    assert "tavily" in get_agent("orchestrator").subagents["research"].mcpServers


def test_unknown_subagent_is_a_load_error(tmp_path: Path):
    (tmp_path / "a.md").write_text("---\ndescription: d\nsubagents: [ghost]\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown subagent 'ghost'"):
        load_agents(tmp_path)


def test_agents_dir_is_in_the_repo():
    assert AGENTS_DIR.is_dir() and (AGENTS_DIR / "orchestrator.md").exists()
    assert not (SETTINGS.project_root / ".claude" / "agents").exists(), "agents live in agents/ now"
