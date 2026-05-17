"""`spawn_agent` MCP tool — lets the orchestrator fire background runs.

Background runs are launched as detached asyncio Tasks. The tool returns
immediately with a run ID the orchestrator can hand back to the user;
status is queryable from the persistence layer or via `bran runs show <id>`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool


@tool(
    "spawn_agent",
    (
        "Launch a background, non-blocking run of another agent. Returns a "
        "run_id you can hand back to the user; the run completes asynchronously "
        "and its result is persisted to the bran SQLite DB. Use this when a "
        "task is long-running, the user asked you to 'do X in the background', "
        "or you want to fan out work in parallel. The `agent` argument must be "
        "the name of a known agent (e.g. 'research', 'summariser', "
        "'research-deep'). Do NOT use this for trivial questions you can answer "
        "yourself or for sub-tasks of your current conversation — for those, "
        "invoke a subagent directly via the Agent tool."
    ),
    {"agent": str, "task": str},
)
async def spawn_agent(args: dict[str, Any]) -> dict[str, Any]:
    # Imported lazily to break the agents <-> runner <-> tools cycle.
    from bran.persistence import RunRecord, insert_run
    from bran.runner import run_agent

    agent = args["agent"]
    task = args["task"]

    # Pre-create the run row so we can hand its ID back to the caller before
    # the actual run starts. The runner will pick up this same record (not
    # insert a new one) so status transitions are visible to anyone polling.
    record = RunRecord.new(agent=agent, task=task)
    insert_run(record)

    async def _go() -> None:
        try:
            await run_agent(agent, task, record=record)
        except Exception:
            # The runner already persists the failure; swallow here so a crash
            # in the background task doesn't tear down the event loop.
            pass

    # Detach: the task lives on the running event loop until completion.
    asyncio.create_task(_go(), name=f"spawn:{record.id}")

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Spawned background run: agent={agent}, run_id={record.id}.\n"
                    f"Check status with `bran runs show {record.id}` or "
                    f"GET /runs/{record.id}."
                ),
            }
        ]
    }


# The orchestrator references this server by attribute, so it must exist at
# import time. Server name "bran" => tool exposed as `mcp__bran__spawn_agent`.
spawn_agent_server = create_sdk_mcp_server(
    name="bran",
    version="0.1.0",
    tools=[spawn_agent],
)
