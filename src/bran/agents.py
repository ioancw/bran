"""Agent registry.

Agents are defined as files in `agents/<name>.md` (see bran.agent_files for
the format) and loaded here into `Agent` objects. One file is the single
source of truth for an agent in BOTH of its forms:

- `Agent` (this module): bran's top-level agent — what the user chats with,
  schedules as a Runner, spawns in the background, or runs from the CLI.
- `AgentDefinition` (from the SDK): the same agent as an *inline subagent*
  another agent may delegate to through the Agent tool. Derived from the
  same spec, so prompts, tools and models can't drift apart.

Agents turn into `ClaudeAgentOptions` for the SDK via `build_options_for()`.
Programmatic registration (`register_agent`) still works for scripts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

from bran.agent_files import AgentSpec, load_agent_dir
from bran.config import SETTINGS
from bran.permissions import WRITE_TOOL_MATCHER, confine_writes_hook
from bran.tools.documents import documents_server
from bran.tools.spawn import spawn_agent_server

AGENTS_DIR = SETTINGS.project_root / "agents"

# PreToolUse hook set that confines file writes to bran's home dir. Attached to
# every agent (the matcher only fires for write tools) so a prompt-injection in
# fetched web content can't write outside the sandbox, including inside a
# delegated subagent. See bran.permissions for the rationale.
_WRITE_CONFINEMENT_HOOKS: dict[str, Any] = {
    "PreToolUse": [HookMatcher(matcher=WRITE_TOOL_MATCHER, hooks=[confine_writes_hook])],
}


# ---------------------------------------------------------------------------
# MCP servers. An agent never declares servers — they follow from the tool
# names it grants (`mcp__<server>__<tool>`).
# ---------------------------------------------------------------------------

# Tools the Tavily MCP server exposes; `mcp__tavily__*` in a file expands to
# these, and they're dropped entirely when there's no TAVILY_API_KEY.
TAVILY_TOOLS = [
    "mcp__tavily__tavily_search",
    "mcp__tavily__tavily_extract",
    "mcp__tavily__tavily_map",
    "mcp__tavily__tavily_crawl",
]


def _tavily_server() -> dict[str, Any] | None:
    """stdio MCP server config for Tavily, or None without a key. Tavily ships
    an official MCP server as the `tavily-mcp` npm package; `npx -y` means no
    global install (Node 18+ is already a hard dep of the Agent SDK)."""
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return None
    return {"command": "npx", "args": ["-y", "tavily-mcp"], "env": {"TAVILY_API_KEY": key}}


def _server_for_prefix(prefix: str) -> tuple[str, Any] | None:
    if prefix == "bran":
        return "bran", spawn_agent_server
    if prefix == "bran_docs":
        return "bran_docs", documents_server
    if prefix == "tavily":
        cfg = _tavily_server()
        return ("tavily", cfg) if cfg else None
    return None


def resolve_tools(tools: list[str]) -> list[str]:
    """Expand `mcp__<server>__*` wildcards, drop MCP tools whose server isn't
    available (no Tavily key), dedupe while keeping order."""
    out: list[str] = []
    for t in tools:
        if t == "mcp__tavily__*":
            expanded = TAVILY_TOOLS if os.getenv("TAVILY_API_KEY") else []
        elif t.startswith("mcp__tavily__") and not os.getenv("TAVILY_API_KEY"):
            expanded = []
        else:
            expanded = [t]
        for e in expanded:
            if e not in out:
                out.append(e)
    return out


def servers_for(tools: list[str]) -> dict[str, Any]:
    """The MCP server configs an agent needs, keyed by server name."""
    servers: dict[str, Any] = {}
    for t in tools:
        if not t.startswith("mcp__"):
            continue
        prefix = t[len("mcp__"):].split("__", 1)[0]
        hit = _server_for_prefix(prefix)
        if hit and hit[0] not in servers:
            servers[hit[0]] = hit[1]
    return servers


# Appended to every agent's system prompt so math expressions in their replies
# get rendered by the web UI's KaTeX integration. Without this, models default
# to plain text (e.g. `x^2`), which doesn't trigger KaTeX and looks worse than
# no notation at all.
_MATH_NOTATION_NOTE = (
    "\n\n## Math notation\n"
    "When your reply contains mathematical expressions, wrap them in LaTeX "
    "delimiters so the web UI renders them with KaTeX:\n"
    "- Inline: `\\(x^2\\)`, `\\(\\pi r^2\\)`, `\\(\\sigma_{xy}\\)`\n"
    "- Display (centred block): `$$E = mc^2$$` or `\\[\\int_0^\\infty e^{-x^2}\\,dx\\]`\n"
    "Do NOT use plain-text notation like `x^2` or `A = P(1+r/n)^(nt)` — that "
    "renders as literal text and looks wrong. Do NOT use single `$...$` "
    "delimiters either; the UI deliberately ignores them so prices in finance "
    "content (e.g. `$0.46 spent`) don't accidentally get parsed as math."
)


@dataclass(frozen=True)
class Agent:
    """A top-level bran agent — what the user invokes by name from any surface."""

    name: str
    description: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    model: str | None = None  # "sonnet" | "opus" | "haiku" or a full model ID
    # dontAsk: tools in `tools` run (they're pre-approved), anything else that
    # would prompt is denied outright — a fixed, explicit tool surface for a
    # headless agent, with no reliance on "nobody is there to approve".
    permission_mode: str = "dontAsk"
    # Sub-agents this agent can delegate to via the SDK's Agent tool. Keys
    # become the names visible to Claude as `subagent_type`.
    subagents: dict[str, AgentDefinition] = field(default_factory=dict)
    # In-process MCP servers exposed to this agent (e.g. spawn_agent).
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    # Skills to preload at startup (subset of .claude/skills/*).
    skills: list[str] = field(default_factory=list)
    max_turns: int | None = None
    # SDK hooks (e.g. PreToolUse write confinement). Maps HookEvent -> matchers.
    hooks: dict[str, Any] | None = None
    # The file this agent was loaded from (None for programmatic agents).
    source: str | None = None


# Built-in Claude Code tools an agent could reach even when not listed in its
# `tools` — allowed_tools only PRE-APPROVES, it doesn't restrict. Anything in
# this set that an agent doesn't list is removed from its request outright
# (disallowed_tools), so e.g. no bran agent ever sees Bash.
BUILTIN_TOOLS = (
    "Bash", "Write", "Edit", "NotebookEdit",
    "WebSearch", "WebFetch", "Agent", "Task",
)


def session_tools(agent: Agent) -> list[str]:
    """Every tool the SESSION must allow: the agent's own plus those of the
    subagents it can delegate to. Allow/deny rules and MCP servers are
    session-wide in Claude Code — a deny on the parent reaches into every
    subagent — so the union is what the SDK options have to describe."""
    out = list(agent.tools)
    for sub in agent.subagents.values():
        for t in sub.tools or []:
            if t not in out:
                out.append(t)
    return out


def deny_list_for(tools: list[str]) -> list[str]:
    """Built-in tools to strip from an agent's request: every known built-in
    it did not explicitly grant ("Agent" and "Task" are one tool across CLI
    versions, so granting either keeps both)."""
    granted = set(tools)
    if "Agent" in granted or "Task" in granted:
        granted |= {"Agent", "Task"}
    return [t for t in BUILTIN_TOOLS if t not in granted]


# ---------------------------------------------------------------------------
# Specs -> agents
# ---------------------------------------------------------------------------

def _render_prompt(spec: AgentSpec, specs: dict[str, AgentSpec]) -> str:
    text = spec.prompt
    if "{{roster}}" in text:
        lines = []
        for name in spec.subagents:
            sub = specs.get(name)
            if sub is not None:
                lines.append(f"- `{name}`: {sub.description}")
        text = text.replace("{{roster}}", "\n".join(lines) if lines else "(no specialists configured)")
    text = text.replace("{{briefings_dir}}", str(SETTINGS.briefings_dir))
    return text + _MATH_NOTATION_NOTE


def _model_for(spec: AgentSpec) -> str | None:
    if spec.model is None:
        return None
    if spec.model == "default":
        return SETTINGS.default_model
    return spec.model


def _subagent_definition(spec: AgentSpec, specs: dict[str, AgentSpec]) -> AgentDefinition:
    tools = resolve_tools(spec.tools)
    return AgentDefinition(
        description=spec.description,
        prompt=_render_prompt(spec, specs),
        tools=tools,
        mcpServers=list(servers_for(tools).keys()),
        model=_model_for(spec),
        maxTurns=spec.max_turns,
    )


def _source_label(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(SETTINGS.project_root).as_posix()
    except ValueError:
        return str(path)


def agent_from_spec(spec: AgentSpec, specs: dict[str, AgentSpec]) -> Agent:
    tools = resolve_tools(spec.tools)
    subagents: dict[str, AgentDefinition] = {}
    for name in spec.subagents:
        sub = specs.get(name)
        if sub is None:
            raise ValueError(f"agent {spec.name!r} lists unknown subagent {name!r}")
        if sub.name == spec.name:
            raise ValueError(f"agent {spec.name!r} cannot be its own subagent")
        subagents[name] = _subagent_definition(sub, specs)
    if subagents and "Agent" not in tools and "Task" not in tools:
        tools = ["Agent", *tools]  # the SDK's delegation tool, required for subagents
    # MCP servers are configured per session, so a subagent's servers must be
    # wired on the parent too (its definition only names them).
    all_tools = list(tools)
    for sub in subagents.values():
        all_tools += [t for t in (sub.tools or []) if t not in all_tools]
    return Agent(
        name=spec.name,
        description=spec.description,
        system_prompt=_render_prompt(spec, specs),
        tools=tools,
        model=_model_for(spec),
        permission_mode=spec.permission_mode,
        subagents=subagents,
        mcp_servers=servers_for(all_tools),
        skills=list(spec.skills),
        max_turns=spec.max_turns,
        hooks=_WRITE_CONFINEMENT_HOOKS,
        source=_source_label(spec.path),
    )


_REGISTRY: dict[str, Agent] = {}


def load_agents(directory: Path | None = None) -> list[Agent]:
    """(Re)load the registry from the agents directory. Programmatic agents
    registered earlier are dropped — call register_agent again after."""
    specs = {s.name: s for s in load_agent_dir(directory or AGENTS_DIR)}
    _REGISTRY.clear()
    for spec in specs.values():
        _REGISTRY[spec.name] = agent_from_spec(spec, specs)
    return list(_REGISTRY.values())


def list_agents() -> list[Agent]:
    return list(_REGISTRY.values())


def get_agent(name: str) -> Agent:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"Unknown agent '{name}'. Known: {known}")
    return _REGISTRY[name]


def register_agent(agent: Agent) -> None:
    """Allow runtime registration — useful when extending bran in user scripts."""
    _REGISTRY[agent.name] = agent


load_agents()


def build_options_for(
    agent: Agent,
    *,
    resume: str | None = None,
    session_id: str | None = None,
    max_turns: int | None = None,
    append_system: str | None = None,
) -> ClaudeAgentOptions:
    """Translate an Agent into the SDK's ClaudeAgentOptions.

    `append_system` (when set) is concatenated onto the agent's system prompt.
    This is how project-scoped chats inject their always-on instructions —
    see web/spa_api.py::chat_stream.

    The global "About the user" instructions (Settings → About me) are layered
    in here because *every* surface — chat, runners, spawns, manual runs, CLI —
    builds its options through this function. Lazy import so module load stays
    free of DB access.
    """
    system_prompt = agent.system_prompt
    from bran.persistence import get_setting

    about = get_setting("user_instructions").strip()
    if about:
        system_prompt = f"{system_prompt}\n\n## About the user\n{about}"
    if append_system:
        system_prompt = f"{system_prompt}\n\n{append_system}"
    allowed = session_tools(agent)
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        # Pre-approve the agent's tools AND its subagents' (rules are
        # session-wide; dontAsk would otherwise deny a subagent's WebSearch).
        allowed_tools=allowed,
        # allowed_tools only pre-approves; this is what actually keeps the
        # ungranted built-ins (Bash above all) out of the whole session's reach.
        disallowed_tools=deny_list_for(allowed),
        model=agent.model,
        permission_mode=agent.permission_mode,
        agents=dict(agent.subagents) if agent.subagents else None,
        mcp_servers=dict(agent.mcp_servers) if agent.mcp_servers else {},
        skills=list(agent.skills) if agent.skills else None,
        resume=resume,
        # A fresh session under a chosen id (used when a chat's SDK transcript
        # was pruned: bran keeps the chat's identity, the CLI starts over).
        session_id=session_id,
        max_turns=max_turns if max_turns is not None else agent.max_turns,
        cwd=str(SETTINGS.project_root),
        hooks=dict(agent.hooks) if agent.hooks else None,
        # Project settings only. bran depends on .claude/ under cwd for slash
        # commands and skills — but the USER's ~/.claude settings (permission
        # allow rules, hooks, MCP servers) must never leak into unattended
        # agent runs: a `Bash(*)` allow rule there would hand every bran agent
        # a shell.
        setting_sources=["project", "local"],
        # Raise the 1MB default so a single large tool result can't abort a run.
        max_buffer_size=SETTINGS.max_buffer_size,
        # Spend ceiling for this run (client-side estimate; the query ends with
        # error_max_budget_usd). Resumed-session history doesn't count.
        max_budget_usd=SETTINGS.run_budget_usd if SETTINGS.run_budget_usd > 0 else None,
        # Bound the inline Agent-tool tree inside this run. The Python SDK
        # merges env into the inherited environment.
        env={
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": str(SETTINGS.subagent_depth),
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": str(SETTINGS.subagent_concurrency),
        },
    )


# ---------------------------------------------------------------------------
# Back-compat aliases (deprecated — kept so any script importing the old names
# from bran.personas still works after they update the module path).
# ---------------------------------------------------------------------------

Persona = Agent  # legacy alias
list_personas = list_agents
get_persona = get_agent
register_persona = register_agent
