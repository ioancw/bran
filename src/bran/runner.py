"""Core run loop. Every surface (CLI, HTTP, scheduler, library) goes through here.

`run_agent()` is the async primary; `run_agent_sync()` wraps it for non-async callers.
Each run is persisted to SQLite (pending -> running -> completed/failed) along with
the Agent SDK session_id so callers can resume conversations.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from bran.agents import build_options_for, get_agent
from bran.config import SETTINGS
from bran.notify import notify_completion
from bran.persistence import RunRecord, get_run, insert_run, update_run


async def run_agent(
    agent: str,
    task: str,
    *,
    resume_session: str | None = None,
    parent_run_id: str | None = None,
    max_turns: int | None = None,
    extra_options: dict[str, Any] | None = None,
    on_message=None,
    record: RunRecord | None = None,
) -> RunRecord:
    """Execute an agent run end-to-end, returning the persisted record.

    `on_message` is an optional sync callback invoked for each SDK message —
    useful for live streaming to a REPL or websocket.

    `record` allows the caller to pre-create the run row (e.g. so the run ID
    can be returned to a user before execution starts, as `spawn_agent` does);
    if provided, it will be transitioned to `running` and updated in place
    rather than re-inserted.
    """
    agent_def = get_agent(agent)  # raises KeyError if unknown
    if record is None:
        record = RunRecord.new(agent=agent, task=task, parent_run_id=parent_run_id)
        record.status = "running"
        insert_run(record)
    else:
        record.status = "running"
        update_run(record)

    options = build_options_for(agent_def, resume=resume_session, max_turns=max_turns)
    if extra_options:
        for k, v in extra_options.items():
            setattr(options, k, v)

    started = time.perf_counter()
    try:
        async for message in query(prompt=task, options=options):
            if on_message is not None:
                on_message(message)
            _absorb_message(record, message)

        # query() iterator finished. If we never saw a ResultMessage the SDK
        # ended early — flag as failed so the caller can react.
        if record.status == "running":
            record.status = "failed"
            record.error = "Agent stream ended without a ResultMessage"
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        record.ended_at = _now_iso()
        update_run(record)
        return record
    except Exception as exc:
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        record.ended_at = _now_iso()
        update_run(record)
        raise
    finally:
        # Fire notifications regardless of how the run ended. notify_completion
        # never raises, so this can't break an otherwise-fine exception path.
        await notify_completion(record)


def run_agent_sync(
    agent: str,
    task: str,
    *,
    resume_session: str | None = None,
    parent_run_id: str | None = None,
    max_turns: int | None = None,
    extra_options: dict[str, Any] | None = None,
) -> RunRecord:
    """Synchronous convenience wrapper. Safe to call from regular scripts and cron."""
    return asyncio.run(
        run_agent(
            agent,
            task,
            resume_session=resume_session,
            parent_run_id=parent_run_id,
            max_turns=max_turns,
            extra_options=extra_options,
        )
    )


async def stream_agent(
    agent: str,
    task: str,
    *,
    resume_session: str | None = None,
    parent_run_id: str | None = None,
) -> AsyncIterator[Any]:
    """Async generator that yields SDK messages while persisting the run.

    Use when you want to consume the message stream directly (e.g. in a REPL or
    websocket handler) rather than just receive the final RunRecord.
    """
    agent_def = get_agent(agent)
    record = RunRecord.new(agent=agent, task=task, parent_run_id=parent_run_id)
    record.status = "running"
    insert_run(record)

    options = build_options_for(agent_def, resume=resume_session)
    started = time.perf_counter()
    try:
        async for message in query(prompt=task, options=options):
            _absorb_message(record, message)
            yield message
        if record.status == "running":
            record.status = "failed"
            record.error = "Agent stream ended without a ResultMessage"
    except Exception as exc:
        record.status = "failed"
        record.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        record.ended_at = _now_iso()
        update_run(record)


def _absorb_message(record: RunRecord, message: Any) -> None:
    """Pull useful fields out of an SDK message into the run record."""
    if isinstance(message, SystemMessage) and message.subtype == "init":
        # The init system message carries the session_id under data — capture
        # it as early as possible so callers can resume even if the run aborts.
        sid = (message.data or {}).get("session_id")
        if sid:
            record.session_id = sid
            update_run(record)
    elif isinstance(message, AssistantMessage):
        # Capture sub-agent delegations as they happen so the run detail view
        # can show "this orchestrator run delegated to research + summariser".
        # Two delegation flavours exist:
        #   - The SDK's built-in Agent tool (renamed from "Task" in v2.1.63)
        #     for in-conversation subagent invocation.
        #   - bran's custom mcp__bran__spawn_agent for fire-and-forget runs.
        for block in message.content:
            if not isinstance(block, ToolUseBlock):
                continue
            if block.name in ("Agent", "Task"):
                sub = (block.input or {}).get("subagent_type")
                if sub:
                    invoked = record.metadata.setdefault("subagents_invoked", [])
                    invoked.append(sub)
                    update_run(record)
            elif block.name == "mcp__bran__spawn_agent":
                target = (block.input or {}).get("agent")
                if target:
                    spawned = record.metadata.setdefault("spawned_runs", [])
                    spawned.append({
                        "agent": target,
                        "task": ((block.input or {}).get("task") or "")[:200],
                    })
                    update_run(record)
    elif isinstance(message, ResultMessage):
        record.session_id = message.session_id or record.session_id
        record.num_turns = message.num_turns
        record.total_cost_usd = message.total_cost_usd
        record.result = message.result
        record.status = "failed" if message.is_error else "completed"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# Helpers used by the REPL to pretty-print streamed content.

def extract_text(message: Any) -> str | None:
    """Return concatenated TextBlock text from an AssistantMessage, if any."""
    if not isinstance(message, AssistantMessage):
        return None
    parts = [b.text for b in message.content if isinstance(b, TextBlock)]
    return "".join(parts) if parts else None


def extract_tool_calls(message: Any) -> list[tuple[str, dict[str, Any]]]:
    """Return list of (tool_name, input) for any ToolUseBlocks in the message."""
    if not isinstance(message, AssistantMessage):
        return []
    return [(b.name, b.input) for b in message.content if isinstance(b, ToolUseBlock)]
