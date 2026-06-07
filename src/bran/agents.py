"""Agent registry.

An *agent* in bran bundles a description, system prompt, allowed tools, model
alias, permission mode, and the sub-agents it can delegate to. Agents turn
into `ClaudeAgentOptions` for the SDK via `build_options_for()`.

Note the deliberate distinction:
- `Agent` (this module): bran's top-level configurable agent.
- `AgentDefinition` (from the SDK): the SDK's subagent config — one of several
  things an `Agent` can hold inside its `subagents` dict.

Add an agent by:
1. Defining it as an `Agent` here, OR
2. Dropping a markdown file in `.claude/agents/<name>.md` (auto-loaded by the SDK).

The two coexist; programmatic agents take precedence on name collision.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

from bran.config import SETTINGS
from bran.permissions import WRITE_TOOL_MATCHER, confine_writes_hook
from bran.tools.spawn import spawn_agent_server


# PreToolUse hook set that confines file writes to bran's home dir. Attached to
# every agent that can write (directly or via a delegated sub-agent) so a
# prompt-injection in fetched web content can't write outside the sandbox.
# See bran.permissions for the rationale.
_WRITE_CONFINEMENT_HOOKS: dict[str, Any] = {
    "PreToolUse": [HookMatcher(matcher=WRITE_TOOL_MATCHER, hooks=[confine_writes_hook])],
}


# ---------------------------------------------------------------------------
# External MCP servers (auto-enabled when their API key is present).
# ---------------------------------------------------------------------------

# Tools the Tavily MCP server exposes. Kept as a constant so agents can opt
# into the full set with a single splat.
TAVILY_TOOLS = [
    "mcp__tavily__tavily_search",
    "mcp__tavily__tavily_extract",
    "mcp__tavily__tavily_map",
    "mcp__tavily__tavily_crawl",
]


def _tavily_server() -> dict[str, Any] | None:
    """Return a stdio MCP server config for Tavily, or None if no key is set.

    Tavily ships an official MCP server as the `tavily-mcp` npm package. We
    invoke it via `npx -y` so users don't need a global install — Node 18+
    is already a hard dep of the Claude Agent SDK so it's always available.
    """
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return None
    return {
        "command": "npx",
        "args": ["-y", "tavily-mcp"],
        "env": {"TAVILY_API_KEY": key},
    }


def _maybe_tavily_servers() -> dict[str, Any]:
    """Return {'tavily': <config>} if the key is set, else {}."""
    server = _tavily_server()
    return {"tavily": server} if server else {}


def _maybe_tavily_tools() -> list[str]:
    """Return the Tavily tool list if the key is set, else []."""
    return TAVILY_TOOLS if os.getenv("TAVILY_API_KEY") else []


# Appended to every agent's system prompt so math expressions in their replies
# get rendered by the web UI's KaTeX integration. Without this, models default
# to plain text (e.g. `x^2` or `A = P(1+r/n)^(nt)`), which doesn't trigger
# KaTeX and looks worse than no notation at all.
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
    permission_mode: str = "acceptEdits"
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


# ---------------------------------------------------------------------------
# Sub-agent definitions (used by the orchestrator).
# ---------------------------------------------------------------------------

RESEARCH_AGENT = AgentDefinition(
    description=(
        "Web research specialist. Use this agent for any task that requires "
        "searching the web, reading articles, comparing sources, or producing "
        "a written summary backed by citations."
    ),
    prompt=(
        "You are a careful web research analyst. For every task:\n"
        "1. Prefer `mcp__tavily__tavily_search` over `WebSearch` when available — "
        "it returns cleaner, more relevant results. Fall back to `WebSearch` only "
        "if Tavily isn't listed in your tools.\n"
        "2. Find at least three independent sources.\n"
        "3. Read the most promising ones in full with `mcp__tavily__tavily_extract` "
        "(preferred) or `WebFetch`.\n"
        "4. Cross-check claims across sources.\n"
        "5. Write a tight, well-structured answer with inline citations as "
        "(Source: <domain>) and a Sources list at the end.\n"
        "Be skeptical of low-quality sources. Note uncertainty explicitly."
        + _MATH_NOTATION_NOTE
    ),
    tools=["WebSearch", "WebFetch", "Read", "Write", "Glob", "Grep", *TAVILY_TOOLS],
    mcpServers=["tavily"] if os.getenv("TAVILY_API_KEY") else [],
    model="sonnet",
)


SUMMARISER_AGENT = AgentDefinition(
    description=(
        "Text summarisation specialist. Use for condensing long documents, "
        "transcripts, or sets of articles into briefings."
    ),
    prompt=(
        "You are a precise summariser. Produce structured briefings:\n"
        "- TL;DR (one sentence)\n"
        "- Key points (3-7 bullets)\n"
        "- Open questions or caveats\n"
        "Preserve every concrete number, name, and date. Never invent facts."
        + _MATH_NOTATION_NOTE
    ),
    tools=["Read", "Glob", "Grep"],
    model="haiku",
)


FINANCE_NEWS_PROMPT = (
    "You are Bran's finance-news briefing agent. Your job is to fetch the latest "
    "headlines from a fixed set of financial RSS feeds and produce a structured "
    "morning digest with a finance lens (markets, central banks, rates, FX, "
    "commodities, M&A, macro).\n\n"
    "## RSS feeds (fetch in parallel)\n"
    "1. BBC Business — https://feeds.bbci.co.uk/news/business/rss.xml\n"
    "2. Bloomberg Markets — https://feeds.bloomberg.com/markets/news.rss\n"
    "3. Financial Times — https://www.ft.com/rss/home/international\n"
    "4. MarketWatch Top Stories — https://feeds.content.dowjones.io/public/rss/mw_topstories\n"
    "5. Investing.com Economy — https://www.investing.com/rss/news_14.rss\n"
    "6. Investing.com Forex — https://www.investing.com/rss/news_1.rss\n\n"
    "Use `WebFetch` for each. If `mcp__tavily__tavily_extract` is in your tools, "
    "prefer it for individual articles you want to read in full — it returns clean "
    "body text rather than rendered HTML.\n\n"
    "## Filter (default: finance)\n"
    "Unless the user passes a different filter in their prompt, apply the **finance** "
    "lens: prioritise markets (equities, bonds, FX, commodities), central banks, "
    "interest rates, monetary policy, investing, M&A, private equity, hedge funds, "
    "and macro economics. Deprioritise pure consumer / lifestyle / corporate-PR news "
    "unless it has a direct market-moving implication. If the user passes specific "
    "topics (e.g. 'oil, rates, UK economy'), lead with stories matching those topics "
    "and flag each with a ★ marker.\n\n"
    "## Output structure (markdown)\n"
    "```\n"
    "# Finance Briefing — <Day, DD Month YYYY> — <HH:MM GMT>\n"
    "**Lens:** <finance | user filter>\n\n"
    "## Top theme\n"
    "<2–3 sentences identifying the dominant cross-source story of the day.>\n\n"
    "## Markets snapshot\n"
    "<3–5 bullets if you can derive index/FX/commodity moves from the headlines, "
    "otherwise skip this section.>\n\n"
    "## BBC Business\n"
    "## Bloomberg Markets\n"
    "## Financial Times\n"
    "## MarketWatch\n"
    "## Economy & Macro (Investing.com)\n"
    "## Bonds & FX (Investing.com)\n"
    "<For each section: top 5 stories formatted as `**Headline** — one-sentence "
    "summary. <link>`>\n\n"
    "## Cross-source highlights\n"
    "<3–5 stories that appear across multiple feeds or are the day's biggest.>\n\n"
    "## Left field\n"
    "<One genuinely surprising, underreported, or off-beat finance/macro story the "
    "user is unlikely to have seen. Include why it's worth attention in 1–2 lines.>\n"
    "```\n\n"
    "## Rules\n"
    "- Deduplicate: same story across feeds → mention once in Cross-source.\n"
    "- One sentence per story summary. Always include the URL.\n"
    "- FT articles require a subscription — flag '(paywall)' next to FT links.\n"
    "- Investing.com feeds are headlines-only; add '(headline only)' when ambiguous.\n"
    "- If a feed fails to load, note it and continue with the others.\n"
    "- End with the feed timestamps so the user can gauge freshness.\n\n"
    "## Always save the briefing\n"
    "Whether invoked interactively or on a schedule, save the final markdown to "
    "`.bran/briefings/briefing_<YYYY-MM-DD>.md` using the `Write` tool BEFORE "
    "returning your reply. The directory already exists. Use UTC date in the "
    "filename. Then return the full briefing as your final message."
    + _MATH_NOTATION_NOTE
)


FINANCE_NEWS_AGENT = AgentDefinition(
    description=(
        "Finance-focused morning news briefing. Fetches BBC Business, Bloomberg "
        "Markets, FT, MarketWatch, and Investing.com (Economy + Forex) feeds and "
        "produces a structured digest emphasising markets, rates, FX, commodities, "
        "and macro. Use for any 'morning briefing', 'market briefing', or 'what "
        "happened overnight in finance' style request."
    ),
    prompt=FINANCE_NEWS_PROMPT,
    tools=["WebFetch", "Write", "Read", *TAVILY_TOOLS],
    mcpServers=["tavily"] if os.getenv("TAVILY_API_KEY") else [],
    model="sonnet",
)


# ---------------------------------------------------------------------------
# Top-level agents.
# ---------------------------------------------------------------------------

ORCHESTRATOR = Agent(
    name="orchestrator",
    description=(
        "Default conversational agent. Chats with the user and delegates focused "
        "subtasks to specialist sub-agents. Can also fire background runs via the "
        "spawn_agent tool when the user wants something done asynchronously."
    ),
    system_prompt=(
        "You are Bran, the orchestrator of a fleet of specialist agents.\n\n"
        "Your role:\n"
        "- Have a normal, helpful conversation with the user.\n"
        "- When a task fits a specialist, invoke them via the Agent tool. The "
        "  current roster is: `research` (web research), `summariser` (briefings), "
        "  `finance-news` (morning finance briefing from RSS feeds).\n"
        "- For 'morning briefing', 'market briefing', or 'what happened overnight "
        "  in finance' requests, delegate to the `finance-news` agent.\n"
        "- For long-running or fire-and-forget tasks that should run ONCE, now, "
        "  use `mcp__bran__spawn_agent` to launch a background run and return "
        "  immediately with its run ID.\n"
        "- To set up a RECURRING or scheduled job (the user says 'schedule', "
        "  'every morning/weekday', 'each week', 'at 7am', 'remind me to run X'), "
        "  use the Runner tools: `mcp__bran__create_runner` (a cron expression for "
        "  recurring, or an ISO datetime for a one-shot), plus `list_runners`, "
        "  `pause_runner`, `resume_runner`, `delete_runner`. Ask the user for any "
        "  missing detail (which agent, what time, attach to a project?) before "
        "  creating, then confirm what you scheduled and when it next fires.\n"
        "- Be concise. Reflect tool/agent results back to the user faithfully.\n"
        "- If the user asks 'what can you do?', list the available agents and "
        "  the surfaces (chat REPL, CLI, HTTP, schedules)."
        + _MATH_NOTATION_NOTE
    ),
    tools=[
        "Read", "Glob", "Grep", "WebSearch", "WebFetch",
        "Agent",  # required to invoke subagents
        "mcp__bran__spawn_agent",
        "mcp__bran__save_project_memory",
        "mcp__bran__create_runner",
        "mcp__bran__list_runners",
        "mcp__bran__pause_runner",
        "mcp__bran__resume_runner",
        "mcp__bran__delete_runner",
        *_maybe_tavily_tools(),
    ],
    model=SETTINGS.default_model,
    subagents={
        "research": RESEARCH_AGENT,
        "summariser": SUMMARISER_AGENT,
        "finance-news": FINANCE_NEWS_AGENT,
    },
    mcp_servers={"bran": spawn_agent_server, **_maybe_tavily_servers()},
    # Gate writes by delegated research/finance-news sub-agents (they fetch
    # untrusted web content). The orchestrator itself holds no Write tool.
    hooks=_WRITE_CONFINEMENT_HOOKS,
)


RESEARCH = Agent(
    name="research",
    description="One-shot web research agent. Same skill set as the sub-agent, callable directly.",
    system_prompt=RESEARCH_AGENT.prompt,
    tools=["WebSearch", "WebFetch", "Read", "Write", "Glob", "Grep", *_maybe_tavily_tools()],
    mcp_servers=_maybe_tavily_servers(),
    model="sonnet",
    hooks=_WRITE_CONFINEMENT_HOOKS,
)


SUMMARISER = Agent(
    name="summariser",
    description="One-shot text summariser.",
    system_prompt=SUMMARISER_AGENT.prompt,
    tools=["Read", "Glob", "Grep"],
    model="haiku",
)


FINANCE_NEWS = Agent(
    name="finance-news",
    description=(
        "Morning finance briefing. Pulls BBC Business, Bloomberg Markets, FT, "
        "MarketWatch, and Investing.com feeds and produces a structured digest. "
        "Saves to `.bran/briefings/briefing_<DATE>.md` on every run — ideal for "
        "scheduling with `bran schedule add morning-finance finance-news ... --cron '0 7 * * *'`."
    ),
    system_prompt=FINANCE_NEWS_PROMPT,
    tools=["WebFetch", "Write", "Read", *_maybe_tavily_tools()],
    mcp_servers=_maybe_tavily_servers(),
    model="sonnet",
    hooks=_WRITE_CONFINEMENT_HOOKS,
)


_REGISTRY: dict[str, Agent] = {
    a.name: a for a in (ORCHESTRATOR, RESEARCH, SUMMARISER, FINANCE_NEWS)
}


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


def build_options_for(
    agent: Agent,
    *,
    resume: str | None = None,
    max_turns: int | None = None,
    append_system: str | None = None,
) -> ClaudeAgentOptions:
    """Translate an Agent into the SDK's ClaudeAgentOptions.

    `append_system` (when set) is concatenated onto the agent's system prompt.
    This is how project-scoped chats inject their always-on instructions —
    see web/routes.py::chat_stream.
    """
    system_prompt = agent.system_prompt
    if append_system:
        system_prompt = f"{system_prompt}\n\n{append_system}"
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=list(agent.tools),
        model=agent.model,
        permission_mode=agent.permission_mode,
        agents=dict(agent.subagents) if agent.subagents else None,
        mcp_servers=dict(agent.mcp_servers) if agent.mcp_servers else {},
        skills=list(agent.skills) if agent.skills else None,
        resume=resume,
        max_turns=max_turns if max_turns is not None else agent.max_turns,
        cwd=str(SETTINGS.project_root),
        hooks=dict(agent.hooks) if agent.hooks else None,
        # Load filesystem settings explicitly rather than relying on the SDK's
        # default (which has changed across versions). bran depends on these for
        # .claude/agents (filesystem subagents), .claude/commands (slash
        # commands in chat), and .claude/skills discovery under cwd.
        setting_sources=["user", "project", "local"],
        # Raise the 1MB default so a single large tool result can't abort a run.
        max_buffer_size=SETTINGS.max_buffer_size,
    )


# ---------------------------------------------------------------------------
# Back-compat aliases (deprecated — kept so any script importing the old names
# from bran.personas still works after they update the module path).
# ---------------------------------------------------------------------------

Persona = Agent  # legacy alias
list_personas = list_agents
get_persona = get_agent
register_persona = register_agent
