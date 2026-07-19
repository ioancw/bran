"""Capture the actual subprocess stderr to see what's crashing claude.exe."""
import asyncio
import io

from bran.personas import RESEARCH_AGENT
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from bran.tools.spawn import spawn_agent_server


async def trial(label: str, **opts) -> None:
    print(f"\n=== {label} ===", flush=True)
    buf = io.StringIO()
    options = ClaudeAgentOptions(
        system_prompt="You are helpful.",
        permission_mode="acceptEdits",
        model="sonnet",
        debug_stderr=buf,
        **opts,
    )
    try:
        async for msg in query(prompt="hi", options=options):
            if isinstance(msg, ResultMessage):
                print(f"OK turns={msg.num_turns}")
                break
    except Exception as e:
        print(f"FAIL {type(e).__name__}: {e}")
    finally:
        captured = buf.getvalue()
        if captured:
            print("--- captured subprocess stderr ---")
            print(captured.encode('ascii', 'replace').decode())
            print("--- end stderr ---")
        else:
            print("(no stderr captured)")


async def main() -> None:
    await trial("bare", allowed_tools=["Read"])
    await trial("with subagents",
                allowed_tools=["Read", "Agent"],
                agents={"research": RESEARCH_AGENT})
    await trial("with mcp",
                allowed_tools=["Read", "mcp__bran__spawn_agent"],
                mcp_servers={"bran": spawn_agent_server})


if __name__ == "__main__":
    asyncio.run(main())
