"""Core run loop. Every surface (CLI, HTTP, scheduler, library) goes through here.

`run_agent()` is the async primary; `run_agent_sync()` wraps it for non-async callers.
Each run is persisted to SQLite (pending -> running -> completed/failed) along with
the Agent SDK session_id so callers can resume conversations.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import traceback
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    ToolUseBlock,
    UserMessage,
    query,
)

from bran.agents import build_options_for, get_agent
from bran.background import current_project_id, current_run_id, run_slots
from bran.config import SETTINGS
from bran.live import close as live_close
from bran.live import publish as live_publish
from bran.notify import notify_completion
from bran.persistence import RunRecord, get_run, insert_run, list_runs, update_run, utcnow_iso

# Events produced INSIDE an inline subagent (messages carrying
# parent_tool_use_id), buffered per child run until its tool result lands.
# Single-process, like bran.live.
_child_events: dict[str, list[dict[str, Any]]] = {}
log = logging.getLogger(__name__)
_AGENT_ID_RE = re.compile(r"agentId:\s*([\w-]+)")
_WORKFLOW_META_RE = re.compile(r"name\s*:\s*['\"]([^'\"]{1,80})['\"]")
# Tool calls that hand work to other agents and therefore become child runs.
DELEGATION_TOOLS = ("Agent", "Task", "Workflow")

# Cap what we store in runs.error — a tool that dumps a huge page into an
# exception message shouldn't bloat the DB row (or slow every list query).
_MAX_ERROR_CHARS = 16_000


class RunTimeoutError(Exception):
    """A run exceeded BRAN_RUN_TIMEOUT and was aborted."""


def _truncate_error(text: str) -> str:
    if len(text) <= _MAX_ERROR_CHARS:
        return text
    return text[:_MAX_ERROR_CHARS] + "\n[… error truncated …]"


def _begin_run(
    agent: str,
    task: str,
    parent_run_id: str | None,
    record: RunRecord | None,
    project_id: str | None = None,
    source: str = "manual",
    schedule_id: str | None = None,
    actor: str | None = None,
) -> RunRecord:
    """Return a freshly-`running` record, persisted.

    If `record` is None we create + insert a new row. If the caller pre-created
    one (e.g. `spawn_agent`, so the run ID can be handed back before execution
    starts), we transition it in place instead of inserting a duplicate.

    `project_id` attributes the run to a workspace (chat's project, schedule's
    project, …) so it shows up in that project's activity.
    """
    if record is None:
        record = RunRecord.new(
            agent=agent, task=task, parent_run_id=parent_run_id,
            project_id=project_id,  # None = standalone run
            source=source,
            schedule_id=schedule_id,
            actor=actor,
        )
        record.status = "running"
        insert_run(record)
    else:
        record.status = "running"
        update_run(record)
    return record


async def _drive(
    record: RunRecord,
    task: str,
    options: ClaudeAgentOptions,
    on_message=None,
) -> AsyncIterator[Any]:
    """Run one query loop against an already-`running` record, yielding each
    SDK message. Finalises timing/status, persists, and fires notifications
    regardless of how the run ends — so every surface (one-shot or streaming)
    gets identical lifecycle handling. Re-raises on error after recording it.
    """
    # Publish this run as the ambient context so in-process tools (spawn_agent)
    # can inherit its project + parent. Reset in finally so it doesn't leak.
    proj_token = current_project_id.set(record.project_id)
    run_token = current_run_id.set(record.id)
    # Live streaming: every run broadcasts its events under its run id so the
    # SPA can watch it execute (/spa/runs/{id}/stream). events_from_message is
    # imported lazily to keep core module load free of the web layer (it's a
    # pure converter — no FastAPI/SDK imports).
    from bran.web.events import STORED_EVENTS_MAX_CHARS, compact_for_storage, events_from_message

    # Durable copy of the transcript. The SDK's JSONL file is Claude Code's,
    # and it prunes old sessions — chat history and run transcripts used to
    # vanish with it. Every event is kept on the run row (trimmed + capped) so
    # /history and /transcript can replay without the file.
    kept: list[dict[str, Any]] = []
    kept_chars = 0
    truncated = False

    def _keep(events: list[dict[str, Any]]) -> None:
        nonlocal kept_chars, truncated
        if truncated:
            return
        for ev in events:
            ev = compact_for_storage(ev)
            size = len(json.dumps(ev, default=str))
            if kept_chars + size > STORED_EVENTS_MAX_CHARS:
                truncated = True
                return
            kept.append(ev)
            kept_chars += size

    def _finish(status: str | None = None, error: str | None = None) -> None:
        if status is not None:
            record.status = status
        if error is not None:
            record.error = error
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        record.ended_at = utcnow_iso()
        if kept:
            record.metadata["events"] = kept
            if truncated:
                record.metadata["events_truncated"] = True
        update_run(record)
        # A subagent whose tool result never arrived (the parent died first)
        # must not sit 'running' forever.
        for child_id in [
            *(record.metadata.get("subagent_runs") or {}).values(),
            *(record.metadata.get("task_runs") or {}).values(),
        ]:
            child = get_run(child_id)
            if child is not None and child.status in ("running", "pending"):
                _finish_subagent_run(
                    child_id, "", True,
                    error=f"Parent run ended ({record.status}) before this subagent returned",
                )

    # Capture the CLI subprocess's stderr so a startup failure records WHY.
    # Without this, the SDK's ProcessError reads "Check stderr output for
    # details" with the details nowhere — a five-hour usage-window rejection
    # (overage disabled) looked identical to a crash. Tail only: it's appended
    # to record.error, which is truncated to 16KB anyway.
    stderr_tail: deque[str] = deque(maxlen=40)
    if options is not None and getattr(options, "stderr", None) is None:
        options.stderr = stderr_tail.append

    started = time.perf_counter()
    # Wall-clock ceiling: a hung SDK subprocess (stuck tool, network stall)
    # would otherwise block this task forever — and with the scheduler's
    # max_instances=1, every subsequent fire of that runner backs up behind it.
    timeout_s = SETTINGS.run_timeout_s
    deadline = (time.monotonic() + timeout_s) if timeout_s > 0 else None
    try:
        stream = query(prompt=task, options=options).__aiter__()
        while True:
            try:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    message = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
                else:
                    message = await stream.__anext__()
            except StopAsyncIteration:
                break
            except (asyncio.TimeoutError, TimeoutError):
                # wait_for cancelled __anext__, which finalises the SDK stream
                # (its own cleanup tears down the subprocess). aclose() makes
                # that deterministic rather than left to GC.
                try:
                    await stream.aclose()
                except Exception:
                    pass
                raise RunTimeoutError(
                    f"Run timed out after {timeout_s}s (BRAN_RUN_TIMEOUT). The SDK "
                    "subprocess may have hung, or the task genuinely needs longer — "
                    "raise the limit or split the task."
                )
            if on_message is not None:
                on_message(message)
            _absorb_message(record, message)
            events = events_from_message(message)
            # Messages from inside an inline subagent belong to its child run:
            # its own transcript and live stream, not the parent's (where the
            # subagent's prose would otherwise read as the parent's reply).
            child_id = _child_run_for(record, message)
            if child_id is not None:
                buf = _child_events.setdefault(child_id, [])
                if len(buf) < 4000:
                    buf.extend(compact_for_storage(ev) for ev in events)
                live_publish(child_id, events)
            else:
                _keep(events)
                live_publish(record.id, events)
            yield message

        # query() iterator finished. If we never saw a ResultMessage the SDK
        # ended early — flag as failed so the caller can react.
        if record.status == "running":
            record.status = "failed"
            record.error = "Agent stream ended without a ResultMessage"
        _finish()
    except asyncio.CancelledError:
        # A background run was cancelled (e.g. user hit stop, see
        # background.cancel_background). CancelledError is a BaseException, so it
        # bypasses the `except Exception` below — handle it explicitly to move
        # the run to a terminal state instead of leaving it stuck "running".
        _finish("cancelled", "Run cancelled")
        raise
    except GeneratorExit:
        # The consumer abandoned this generator without cancelling us — e.g. the
        # browser dropped the chat SSE mid-run and the handler's `async for`
        # never resumed. Finalization raises GeneratorExit at the `yield`; it's
        # a BaseException, so without this arm the run row would sit "running"
        # until the next restart's reconciliation and the SDK subprocess would
        # be left to GC.
        _finish("cancelled", "Run abandoned by client (stream consumer went away)")
        try:
            await stream.aclose()
        except Exception:
            pass
        raise
    except RunTimeoutError as exc:
        _finish("failed", str(exc))  # clean message — the traceback adds nothing here
        raise
    except Exception as exc:
        record.status = "failed"
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        if stderr_tail:
            detail += "\n--- CLI stderr (tail) ---\n" + "\n".join(stderr_tail)
        _finish("failed", _truncate_error(detail))
        raise
    finally:
        current_project_id.reset(proj_token)
        current_run_id.reset(run_token)
        # End the live stream however the run finished — subscribers wake and
        # refetch the canonical stored transcript.
        live_close(record.id)
        # Fire notifications regardless of how the run ended. notify_completion
        # never raises, so this can't break an otherwise-fine exception path.
        await notify_completion(record)
        # Headless fan-in: a finished spawn may complete its batch, and a
        # finished chat turn may find its spawns already done — either way the
        # server (not the browser) owns synthesis now. Never raises; lazy
        # import breaks the runner <-> synthesis cycle.
        from bran.synthesis import check_after_run

        check_after_run(record)


async def run_agent(
    agent: str,
    task: str,
    *,
    resume_session: str | None = None,
    parent_run_id: str | None = None,
    max_turns: int | None = None,
    extra_options: dict[str, Any] | None = None,
    append_system: str | None = None,
    on_message=None,
    record: RunRecord | None = None,
    project_id: str | None = None,
    source: str = "manual",
    schedule_id: str | None = None,
    actor: str | None = None,
) -> RunRecord:
    """Execute an agent run end-to-end, returning the persisted record.

    `on_message` is an optional sync callback invoked for each SDK message —
    useful for live streaming to a REPL or websocket.

    `record` allows the caller to pre-create the run row (e.g. so the run ID
    can be returned to a user before execution starts, as `spawn_agent` does);
    if provided, it will be transitioned to `running` and updated in place
    rather than re-inserted.

    `project_id` attributes a freshly-created run to a workspace (ignored when
    `record` is supplied — the caller already set the record's project).
    """
    agent_def = get_agent(agent)  # raises KeyError if unknown

    # Background work (spawns, runners, manual, synthesis) queues behind the
    # fleet's concurrency gate; the row sits 'pending' while it waits so the
    # UI shows a queue, not a stall. Inline (wait=true) spawns are consumed by
    # the turn that made them and must not wait on a slot that turn may hold.
    effective_source = record.source if record is not None else source
    gated = not (record is not None and record.metadata.get("inline"))
    if gated:
        if record is None:
            record = RunRecord.new(
                agent=agent, task=task, parent_run_id=parent_run_id,
                project_id=project_id, source=effective_source,
                schedule_id=schedule_id, actor=actor,
            )
            insert_run(record)
        async with run_slots():
            return await _execute(
                agent_def, agent, task, record, resume_session, max_turns,
                extra_options, append_system, on_message,
            )
    return await _execute(
        agent_def, agent, task, record, resume_session, max_turns,
        extra_options, append_system, on_message,
    )


async def _execute(
    agent_def, agent, task, record, resume_session, max_turns,
    extra_options, append_system, on_message,
) -> RunRecord:
    record = _begin_run(agent, task, record.parent_run_id, record)
    options = build_options_for(
        agent_def, resume=resume_session, max_turns=max_turns,
        append_system=append_system,
    )
    if extra_options:
        for k, v in extra_options.items():
            # Guard against silently setting a misspelled option name (which a
            # plain setattr would happily accept and then ignore at runtime).
            if not hasattr(options, k):
                raise ValueError(f"Unknown ClaudeAgentOptions field: {k!r}")
            setattr(options, k, v)

    # Drain the stream; _drive handles persistence + notification.
    async for _ in _drive(record, task, options, on_message):
        pass
    return record


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
    session_id: str | None = None,
    parent_run_id: str | None = None,
    append_system: str | None = None,
    max_turns: int | None = None,
    on_message=None,
    project_id: str | None = None,
    source: str = "chat",
    model: str | None = None,
) -> AsyncIterator[Any]:
    """Async generator that yields SDK messages while persisting the run.

    Use when you want to consume the message stream directly (e.g. in a REPL or
    websocket handler) rather than just receive the final RunRecord. Shares the
    exact lifecycle of `run_agent` via `_drive` — including completion
    notifications, which earlier diverged and silently skipped the chat path.

    `append_system` is concatenated onto the agent's system prompt — chat
    surface uses this to layer a project's instructions onto every message.
    `project_id` attributes the run to the chat's workspace.
    """
    agent_def = get_agent(agent)
    record = _begin_run(agent, task, parent_run_id, None, project_id, source)
    options = build_options_for(
        agent_def, resume=resume_session, session_id=session_id, max_turns=max_turns,
        append_system=append_system, model=model,
    )
    if model:
        record.metadata["model"] = model  # the override asked for; models used land at the end
        update_run(record)
    gen = _drive(record, task, options, on_message)
    try:
        async for message in gen:
            yield message
    finally:
        # `async for` does NOT close an abandoned iterator — if our consumer
        # walks away (GeneratorExit lands at the yield above), _drive would be
        # left to nondeterministic GC finalization with the run still
        # "running". Close it explicitly so its GeneratorExit arm finalizes
        # the record now. No-op when the stream already finished normally.
        await gen.aclose()


def _child_run_for(record: RunRecord, message: Any) -> str | None:
    """The child run id for a message produced inside an inline subagent."""
    ptid = getattr(message, "parent_tool_use_id", None)
    if not ptid:
        return None
    return (record.metadata.get("subagent_runs") or {}).get(ptid)


def _workflow_identity(inp: dict[str, Any]) -> tuple[str, str]:
    """(agent label, task) for a Workflow tool call: a saved workflow's name,
    else the `name` in the script's meta, else the script's first line."""
    name = str(inp.get("name") or "").strip()
    script = str(inp.get("script") or "")
    if not name:
        m = _WORKFLOW_META_RE.search(script[:2000])
        name = m.group(1) if m else "workflow"
    desc = ""
    m = re.search(r"description\s*:\s*['\"]([^'\"]{1,300})['\"]", script[:3000])
    if m:
        desc = m.group(1)
    elif inp.get("args") is not None:
        desc = f"args: {json.dumps(inp.get('args'), default=str)[:300]}"
    return f"workflow:{name}", desc or f"Workflow {name}"


def _begin_subagent_run(record: RunRecord, block: ToolUseBlock) -> None:
    """An Agent-tool call (or a Workflow run) is a delegation: record it as
    a child run so the fleet is visible — who was asked what, when, and what
    came back. A workflow's own agents then hang under the workflow's run."""
    inp = block.input or {}
    if block.name == "Workflow":
        agent, task = _workflow_identity(inp)
    else:
        agent = str(inp.get("subagent_type") or "general-purpose")
        task = str(inp.get("prompt") or inp.get("description") or "")
    child = RunRecord.new(
        agent=agent,
        task=task,
        parent_run_id=record.id,
        project_id=record.project_id,
        source="subagent",
    )
    child.status = "running"
    child.metadata["tool_use_id"] = block.id
    child.metadata["depth"] = int(record.metadata.get("depth", 0)) + 1
    if block.name == "Workflow":
        child.metadata["workflow"] = True
        if inp.get("args") is not None:
            child.metadata["workflow_args"] = inp.get("args")
    insert_run(child)
    record.metadata.setdefault("subagent_runs", {})[block.id] = child.id


def _task_parent(record: RunRecord, message: Any) -> RunRecord:
    """Where a task (a workflow's agent, or a background subagent) belongs:
    under the delegation call that spawned it when we recorded one, else
    directly under this run."""
    tool_use_id = getattr(message, "tool_use_id", None)
    child_id = (record.metadata.get("subagent_runs") or {}).get(tool_use_id) if tool_use_id else None
    if child_id:
        child = get_run(child_id)
        if child is not None:
            return child
    return record


def _begin_task_run(record: RunRecord, message: TaskStartedMessage) -> None:
    """A task_started event: an agent a Workflow script spawned, or a
    background Agent-tool subagent. Each becomes a child run of the workflow
    (or of the delegation) it belongs to."""
    task_id = getattr(message, "task_id", None)
    if not task_id:
        return
    tasks = record.metadata.setdefault("task_runs", {})
    if task_id in tasks:
        return
    tool_use_id = getattr(message, "tool_use_id", None)
    sub_runs = record.metadata.get("subagent_runs") or {}
    parent = _task_parent(record, message)
    if parent is record and tool_use_id in sub_runs:
        return  # the Agent-tool child already stands for this task
    if parent is not record and not parent.metadata.get("workflow"):
        # A background Agent-tool subagent: the delegation row already exists.
        tasks[task_id] = sub_runs[tool_use_id]
        update_run(record)
        return
    task_type = str(getattr(message, "task_type", None) or "")
    if parent is not record and task_type in ("local_workflow", "workflow", ""):
        # The workflow itself runs as one background task: that task is the
        # workflow row we already have, now with the script's description.
        tasks[task_id] = parent.id
        desc = str(getattr(message, "description", None) or "").strip()
        if desc:
            parent.task = desc
            update_run(parent)
        update_run(record)
        return
    child = RunRecord.new(
        agent=str(getattr(message, "task_type", None) or "agent").replace("_", "-"),
        task=str(getattr(message, "description", None) or ""),
        parent_run_id=parent.id,
        project_id=record.project_id,
        source="subagent",
    )
    child.status = "running"
    child.metadata["task_id"] = task_id
    child.metadata["depth"] = int(parent.metadata.get("depth", 0)) + 1
    insert_run(child)
    tasks[task_id] = child.id
    update_run(record)


_TERMINAL_TASK = frozenset({"completed", "failed", "stopped", "killed"})


def _finish_task_run(record: RunRecord, task_id: str | None, status: str | None, summary: str,
                     output_file: str | None = None, usage: Any = None) -> None:
    """Close a task's child run on a terminal status. The SDK warns that a
    task's end can arrive as a task_notification OR only as a task_updated
    whose patch.status is terminal, so both feed through here."""
    child_id = (record.metadata.get("task_runs") or {}).get(task_id) if task_id else None
    if not child_id:
        return
    status = str(status or "").lower()
    if status not in _TERMINAL_TASK:
        return
    child = get_run(child_id)
    if child is None or child.status in ("completed", "failed"):
        return  # already closed by the other message (or the tool result)
    if child.metadata.get("workflow"):
        _finish_workflow_run(child, status, summary, output_file, usage)
        return
    if child_id in (record.metadata.get("subagent_runs") or {}).values():
        return  # an Agent-tool delegation: its tool_result closes it
    is_error = status != "completed"
    _finish_subagent_run(child_id, summary, is_error,
                         error=None if not is_error else f"Task ended: {status}")


# The Workflow tool answers at once with this boilerplate and does the work
# as a background task; the real end is the task notification.
_WF_BACKGROUND_MARKERS = ("You will be notified when it completes", "Run ID: wf_")
_WF_TRANSCRIPT_RE = re.compile(r"Transcript dir:\s*(\S+)")
_WF_RUN_ID_RE = re.compile(r"Run ID:\s*(wf_[\w-]+)")
_WF_OUTPUT_CAP = 512 * 1024


def _note_workflow_started(child: RunRecord, text: str) -> bool:
    """If a Workflow tool result only says the run has started, keep the row
    open and remember where its transcript lives. True when handled."""
    if not any(marker in text for marker in _WF_BACKGROUND_MARKERS):
        return False
    m = _WF_TRANSCRIPT_RE.search(text)
    if m:
        child.metadata["transcript_dir"] = m.group(1)
    m = _WF_RUN_ID_RE.search(text)
    if m:
        child.metadata["workflow_run_id"] = m.group(1)
    update_run(child)
    return True


def _workflow_output(output_file: str | None) -> tuple[str | None, dict[str, Any]]:
    """(result text, summary) from the task's output file: the script's
    return value, with a markdown `report` pulled out when there is one."""
    if not output_file:
        return None, {}
    try:
        with open(output_file, encoding="utf-8") as fh:
            raw = fh.read(_WF_OUTPUT_CAP + 1)
    except OSError:
        return None, {}
    truncated = len(raw) > _WF_OUTPUT_CAP
    raw = raw[:_WF_OUTPUT_CAP]
    try:
        data = json.loads(raw)
    except ValueError:
        return raw, {"truncated": truncated}
    if not isinstance(data, dict):
        return json.dumps(data, indent=2, ensure_ascii=False), {}
    summary = {k: data[k] for k in ("summary", "agentCount", "logs", "error") if k in data}
    result = data.get("result", data)
    if isinstance(result, dict):
        for key in ("report", "markdown", "text"):
            if isinstance(result.get(key), str) and result[key].strip():
                return result[key], summary
        return json.dumps(result, indent=2, ensure_ascii=False), summary
    if isinstance(result, str):
        return result, summary
    return json.dumps(result, indent=2, ensure_ascii=False), summary


def _finish_workflow_run(child: RunRecord, status: str, summary: str, output_file: str | None,
                         usage: Any) -> None:
    text, info = _workflow_output(output_file)
    if info:
        child.metadata["workflow_summary"] = info
    if isinstance(usage, dict):
        child.metadata["task_usage"] = dict(usage)
    update_run(child)  # _finish_subagent_run re-reads the row
    is_error = status != "completed" or bool(info.get("error"))
    error = None
    if is_error:
        error = str(info.get("error") or summary or f"Workflow ended: {status}")
    _finish_subagent_run(child.id, text or summary, is_error, error=error)
    try:
        _import_workflow_journal(child)
    except Exception:  # noqa: BLE001 — the journal is a bonus; never fail the run over it
        log.exception("Could not import the workflow journal for run %s", child.id)


def _agent_span(transcript_dir: str, agent_id: str) -> tuple[str | None, str | None]:
    """First and last timestamps in the agent's own transcript."""
    path = os.path.join(transcript_dir, f"agent-{agent_id}.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None, None

    def ts(line: str) -> str | None:
        try:
            v = json.loads(line).get("timestamp")
        except ValueError:
            return None
        return str(v) if v else None

    first = next((t for t in (ts(ln) for ln in lines[:5]) if t), None)
    last = next((t for t in (ts(ln) for ln in reversed(lines[-5:])) if t), None)
    return first, last


def _import_workflow_journal(wf: RunRecord) -> None:
    """Every agent the script spawned becomes a child run of the workflow,
    from the SDK's journal: label, phase, agent type and the value it
    returned. Agents without a result (skipped, or dead) are failed."""
    tdir = wf.metadata.get("transcript_dir")
    if not tdir or not os.path.isdir(tdir):
        return
    try:
        with open(os.path.join(tdir, "journal.jsonl"), encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    existing = {
        r.metadata.get("workflow_agent_id"): r
        for r in list_runs(parent_run_id=wf.id, limit=2000)
        if r.metadata.get("workflow_agent_id")
    }
    runs: dict[str, RunRecord] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        agent_id = str(entry.get("agentId") or "")
        kind = entry.get("type")
        if not agent_id or kind not in ("started", "result"):
            continue
        if kind == "started":
            if agent_id in existing or agent_id in runs:
                continue
            label = str(entry.get("label") or "agent")
            phase = str(entry.get("phase") or "")
            agent_type = "workflow-agent"
            try:
                with open(os.path.join(tdir, f"agent-{agent_id}.meta.json"), encoding="utf-8") as fh:
                    meta = json.load(fh)
                if isinstance(meta, dict) and meta.get("agentType") not in (None, "", "workflow-subagent"):
                    agent_type = str(meta["agentType"])
            except (OSError, ValueError):
                pass
            run = RunRecord.new(
                agent=agent_type,
                task=f"{phase}: {label}" if phase else label,
                parent_run_id=wf.id,
                project_id=wf.project_id,
                source="subagent",
            )
            run.status = "running"
            run.metadata.update({
                "workflow_agent_id": agent_id,
                "phase": phase,
                "label": label,
                "depth": int(wf.metadata.get("depth", 0)) + 1,
            })
            first, _ = _agent_span(tdir, agent_id)
            if first:
                run.started_at = first
            runs[agent_id] = run
        else:
            run = runs.get(agent_id) or existing.get(agent_id)
            if run is None or run.status in ("completed", "failed"):
                continue
            result = entry.get("result")
            if result is None:
                run.status, run.error = "failed", "The agent returned nothing (skipped, or stopped on an error)"
            else:
                run.status = "completed"
                run.result = result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False)
            _, last = _agent_span(tdir, agent_id)
            run.ended_at = last or utcnow_iso()
            _set_duration(run)
    for run in runs.values():
        if run.status == "running":
            run.status, run.error = "failed", "The workflow ended before this agent returned"
            run.ended_at = wf.ended_at or utcnow_iso()
            _set_duration(run)
        insert_run(run)
    for run in existing.values():
        if run.status != "running":
            update_run(run)


def _set_duration(run: RunRecord) -> None:
    try:
        from datetime import datetime

        run.duration_ms = int(
            (datetime.fromisoformat(run.ended_at) - datetime.fromisoformat(run.started_at)).total_seconds() * 1000
        )
    except (TypeError, ValueError):
        pass


def _finish_subagent_run(child_id: str, text: str, is_error: bool, *, error: str | None = None) -> None:
    child = get_run(child_id)
    if child is None:
        return
    child.status = "failed" if is_error else "completed"
    child.result = text or None
    if is_error:
        child.error = error or (text[:2000] if text else "Subagent reported an error")
    child.ended_at = utcnow_iso()
    try:
        from datetime import datetime

        child.duration_ms = int(
            (datetime.fromisoformat(child.ended_at) - datetime.fromisoformat(child.started_at)).total_seconds() * 1000
        )
    except ValueError:
        pass
    m = _AGENT_ID_RE.search(text or "")
    if m:
        child.metadata["agent_id"] = m.group(1)  # resumable via the parent session
    events = _child_events.pop(child_id, None)
    if events:
        child.metadata["events"] = events
    update_run(child)
    live_close(child_id)


def _tool_results(message: Any) -> list[tuple[str, str, bool]]:
    """(tool_use_id, text, is_error) for each tool_result block in a user
    message — the SDK hands these as dicts, occasionally as typed blocks."""
    from bran.web.events import _text_from_tool_result_content

    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    out: list[tuple[str, str, bool]] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") != "tool_result":
                continue
            out.append((
                str(block.get("tool_use_id") or ""),
                _text_from_tool_result_content(block.get("content")),
                bool(block.get("is_error")),
            ))
        elif getattr(block, "tool_use_id", None):
            out.append((
                str(block.tool_use_id),
                _text_from_tool_result_content(getattr(block, "content", None)),
                bool(getattr(block, "is_error", False)),
            ))
    return out


def _absorb_message(record: RunRecord, message: Any) -> None:
    """Pull useful fields out of an SDK message into the run record."""
    if isinstance(message, TaskStartedMessage):
        _begin_task_run(record, message)
        return
    if isinstance(message, TaskNotificationMessage):
        _finish_task_run(record, message.task_id, message.status, str(message.summary or ""),
                         getattr(message, "output_file", None), getattr(message, "usage", None))
        return
    if isinstance(message, TaskUpdatedMessage):
        status = message.status or (message.patch or {}).get("status")
        _finish_task_run(record, message.task_id, status, "")
        return
    if isinstance(message, UserMessage):
        runs_map = record.metadata.get("subagent_runs") or {}
        for tool_use_id, text, is_error in _tool_results(message):
            if tool_use_id not in runs_map:
                continue
            child = get_run(runs_map[tool_use_id])
            if child is not None and child.metadata.get("workflow") and not is_error and _note_workflow_started(child, text):
                continue
            _finish_subagent_run(runs_map[tool_use_id], text, is_error)
        return
    if getattr(message, "parent_tool_use_id", None):
        return  # a subagent's own tool calls aren't the parent's delegations
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
            if block.name in DELEGATION_TOOLS:
                sub = (block.input or {}).get("subagent_type")
                if sub:
                    invoked = record.metadata.setdefault("subagents_invoked", [])
                    invoked.append(sub)
                _begin_subagent_run(record, block)
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
            elif block.name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                # Artifacts: files this run produced/modified. Only record
                # targets inside the sanctioned write roots (bran_home + the
                # ambient project's work_dir) — a path outside them was denied
                # by the confinement hook, so it never became a file. Stored in
                # the artifacts table (add_artifact is idempotent per path).
                from bran.permissions import allowed_write_target
                from bran.persistence import add_artifact

                raw = (block.input or {}).get("file_path") or (block.input or {}).get("notebook_path")
                target_path = allowed_write_target(raw) if raw else None
                if target_path is not None:
                    add_artifact(record.id, str(target_path))
    elif isinstance(message, ResultMessage):
        usage = getattr(message, "model_usage", None)
        if isinstance(usage, dict) and usage:
            record.metadata["models"] = sorted(str(k) for k in usage)
        record.session_id = message.session_id or record.session_id
        record.num_turns = message.num_turns
        record.total_cost_usd = message.total_cost_usd
        record.result = message.result
        record.status = "failed" if message.is_error else "completed"


