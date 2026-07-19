"""Narrow down which orchestrator config feature is crashing the CLI subprocess."""
import asyncio
import os

from bran.personas import RESEARCH_AGENT, SUMMARISER_AGENT
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from bran.tools.spawn import spawn_agent_server

# Capture the CLI subprocess's stderr so we can see what crashed it.
os.environ["CLAUDE_CODE_DEBUG"] = "1"


async def trial(label: str, **opts) -> None:
    print(f"\n--- {label} ---", flush=True)

    # Capture every line the CLI subprocess prints to stderr so we can see
    # the actual crash message rather than just the exit code.
    async def grab(line: str) -> None:
        print(f"  [stderr] {line.rstrip()}", flush=True)

    options = ClaudeAgentOptions(
        system_prompt="You are Bran, a helpful assistant.",
        permission_mode="acceptEdits",
        model="sonnet",
        stderr=grab,
        **opts,
    )
    try:
        async for msg in query(prompt="Say hi in one short sentence.", options=options):
            if isinstance(msg, ResultMessage):
                print(f"  OK ({msg.num_turns} turns): {(msg.result or '').encode('ascii','replace').decode()}")
                return
        print("  ENDED without ResultMessage")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")


async def main() -> None:
    # A: bare orchestrator config (no subagents, no MCP) — control
    await trial(
        "A: bare",
        allowed_tools=["Read", "Glob", "Grep"],
    )

    # B: + subagents only
    await trial(
        "B: + subagents",
        allowed_tools=["Read", "Glob", "Grep", "Agent"],
        agents={"research": RESEARCH_AGENT, "summariser": SUMMARISER_AGENT},
    )

    # C: + in-process MCP only
    await trial(
        "C: + mcp_servers",
        allowed_tools=["Read", "Glob", "Grep", "mcp__bran__spawn_agent"],
        mcp_servers={"bran": spawn_agent_server},
    )

    # D: both (full orchestrator)
    await trial(
        "D: + both",
        allowed_tools=["Read", "Glob", "Grep", "Agent", "mcp__bran__spawn_agent"],
        agents={"research": RESEARCH_AGENT, "summariser": SUMMARISER_AGENT},
        mcp_servers={"bran": spawn_agent_server},
    )


if __name__ == "__main__":
    asyncio.run(main())
