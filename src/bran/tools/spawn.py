"""bran's in-process MCP tools.

Exposed as the `bran` MCP server; tools become `mcp__bran__<name>` to agents.

Tools:
    - spawn_agent: fire a background run of another agent
    - save_project_memory: pin a fact/rule to a project's persistent instructions
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from bran.background import current_project_id, current_run_id, spawn_background
from bran.tools.schedules import RUNNER_TOOLS


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
    # Inherit the originating chat's project + link to its run (ambient context
    # set by runner._drive); both are None when spawned outside a run.
    record = RunRecord.new(
        agent=agent, task=task,
        parent_run_id=current_run_id.get(),
        project_id=current_project_id.get(),
        source="spawn",
    )
    insert_run(record)

    async def _go() -> None:
        try:
            await run_agent(agent, task, record=record)
        except Exception:
            # The runner already persists the failure; swallow here so a crash
            # in the background task doesn't tear down the event loop.
            pass

    # Detach: the task lives on the running event loop until completion.
    # spawn_background keeps a strong reference so it can't be GC'd mid-run.
    spawn_background(_go(), name=f"spawn:{record.id}")

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


# ---------------------------------------------------------------------------
# save_project_memory: pin a fact / rule to a project's persistent instructions
# ---------------------------------------------------------------------------

@tool(
    "save_project_memory",
    (
        "Pin a memory to a bran project so every future chat in that project "
        "sees it in its system prompt. Use this when the user says 'remember "
        "that…', invokes /remember, or when a fact is clearly worth preserving "
        "across conversations (style preferences, project context, vocabulary, "
        "ground rules). The project_id is given to you in the system prompt — "
        "do NOT make one up. `text` should be one or two concise declarative "
        "sentences; the tool appends it to the project's existing memory "
        "verbatim, so phrase it well."
    ),
    {"project_id": str, "text": str},
)
async def save_project_memory(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import add_project_memory, get_project

    project_id = args["project_id"]
    text = (args.get("text") or "").strip()

    if not text:
        return {"content": [{"type": "text", "text": "Error: empty memory text — nothing to save."}]}

    project = get_project(project_id)
    if project is None:
        return {"content": [{"type": "text",
                             "text": f"Error: no project with id {project_id!r}."}]}

    add_project_memory(project_id, text)
    return {
        "content": [{
            "type": "text",
            "text": f"Pinned a memory to project '{project.name}'.",
        }]
    }


# The orchestrator references this server by attribute, so it must exist at
# import time. Server name "bran" => tools exposed as `mcp__bran__<name>`.
spawn_agent_server = create_sdk_mcp_server(
    name="bran",
    version="0.1.0",
    tools=[spawn_agent, save_project_memory, *RUNNER_TOOLS],
)
