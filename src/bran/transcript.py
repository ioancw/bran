"""Read SDK session JSONL files and render them as a structured timeline.

The Claude Agent SDK writes the full message-by-message stream for every
session to disk at:

    ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl

…and sub-agent transcripts (spawned via the `Agent` tool) live at:

    ~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-<id>.jsonl

bran's `runs` table stores only the final `result`. The transcript module lets
us reconstruct the full timeline of user prompts, assistant text, tool calls,
tool results, and thinking blocks for any past run — and surface them in the
web UI so the user can see what an agent actually did, not just what it
finished with.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Locating session files on disk
# ---------------------------------------------------------------------------

def _claude_projects_dirs() -> list[Path]:
    """Possible locations of `~/.claude/projects`.

    On Windows + WSL the same physical bran checkout may have produced session
    files in multiple Claude Code installations (Windows-side and WSL-side),
    each storing them under a different encoded-cwd directory. Search both.
    """
    candidates = []
    home = Path.home()
    candidates.append(home / ".claude" / "projects")
    # Linux home when running inside WSL: also try /home/<user>/.claude/projects
    # (already covered by Path.home() in that env, but harmless to dedupe later).
    return [p for p in candidates if p.exists()]


def find_session_file(session_id: str) -> Path | None:
    """Locate the main JSONL transcript for a session id, searching across all
    encoded-cwd directories under ~/.claude/projects.

    Returns None if the file doesn't exist on this machine (which happens for
    sessions created on another host, or for the orphaned smoke-test rows that
    were never actually sent to the SDK).
    """
    if not session_id:
        return None
    for root in _claude_projects_dirs():
        for candidate in root.glob(f"*/{session_id}.jsonl"):
            return candidate
    return None


def find_subagent_files(session_id: str) -> list[Path]:
    """Return all sub-agent JSONL files associated with a parent session.

    Sub-agents (spawned via the SDK's `Agent` tool) get their own self-contained
    transcript files under `<session-id>/subagents/agent-<short-id>.jsonl`.
    """
    main = find_session_file(session_id)
    if main is None:
        return []
    sub_dir = main.parent / session_id / "subagents"
    if not sub_dir.exists():
        return []
    return sorted(sub_dir.glob("agent-*.jsonl"))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """One renderable entry in the timeline view.

    Kinds:
        - "user_text":    plain user prompt
        - "assistant_text": markdown-rendered assistant reply
        - "tool_call":    a ToolUseBlock the assistant emitted
        - "tool_result":  the result of a previous tool_call (paired by id)
        - "thinking":     extended-reasoning content (collapsed by default)
        - "delegation":   a tool_call where name in {Agent, Task} — special-cased
                          so the UI can offer a "view sub-agent transcript" link
    """

    kind: str
    timestamp: str | None = None
    text: str | None = None  # for *_text / thinking
    tool_name: str | None = None  # for tool_call / delegation
    tool_input: dict[str, Any] | None = None
    tool_id: str | None = None
    tool_is_error: bool | None = None  # for tool_result
    subagent_type: str | None = None  # for delegation
    raw: dict[str, Any] = field(default_factory=dict)  # debugging escape hatch


_TOOL_TEXT_SKIP = {"file-history-snapshot", "permission-mode", "last-prompt", "attachment"}


def parse_transcript(jsonl_path: Path) -> list[Entry]:
    """Parse one JSONL file into a flat timeline of `Entry` objects.

    Lines we don't render (file snapshots, permission-mode, attachments,
    last-prompt pointers) are skipped silently. Anything else is mapped to one
    or more Entry items in order of appearance.
    """
    entries: list[Entry] = []
    with jsonl_path.open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t in _TOOL_TEXT_SKIP:
                continue
            ts = msg.get("timestamp")
            if t == "user":
                entries.extend(_explode_user(msg, ts))
            elif t == "assistant":
                entries.extend(_explode_assistant(msg, ts))
            # else: silently ignore unknown types so future SDK versions
            # don't break the parser.
    return entries


def _explode_user(msg: dict[str, Any], ts: str | None) -> Iterator[Entry]:
    """User messages are either plain text prompts or wrap tool_result blocks."""
    content = msg.get("message", {}).get("content")
    if isinstance(content, str):
        yield Entry(kind="user_text", timestamp=ts, text=content, raw=msg)
        return
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                result_content = block.get("content")
                # tool_result content can be a string or a list of content blocks.
                if isinstance(result_content, list):
                    parts = []
                    for c in result_content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(c.get("text", ""))
                    text = "\n".join(parts)
                else:
                    text = str(result_content or "")
                yield Entry(
                    kind="tool_result",
                    timestamp=ts,
                    text=text,
                    tool_id=block.get("tool_use_id"),
                    tool_is_error=bool(block.get("is_error")),
                    raw=block,
                )
            elif btype == "text":
                yield Entry(kind="user_text", timestamp=ts, text=block.get("text", ""), raw=msg)
            # other block types (image, etc.) — skip for v1


def _explode_assistant(msg: dict[str, Any], ts: str | None) -> Iterator[Entry]:
    """Assistant messages are a list of typed content blocks."""
    content = msg.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            yield Entry(kind="assistant_text", timestamp=ts, text=block.get("text", ""), raw=block)
        elif btype == "thinking":
            yield Entry(kind="thinking", timestamp=ts, text=block.get("thinking", ""), raw=block)
        elif btype == "tool_use":
            name = block.get("name", "")
            input_ = block.get("input") or {}
            # The SDK's built-in delegation tool was renamed from Task to Agent
            # in v2.1.63; match both for resilience.
            if name in ("Agent", "Task"):
                yield Entry(
                    kind="delegation",
                    timestamp=ts,
                    tool_name=name,
                    tool_input=input_,
                    tool_id=block.get("id"),
                    subagent_type=input_.get("subagent_type"),
                    raw=block,
                )
            else:
                yield Entry(
                    kind="tool_call",
                    timestamp=ts,
                    tool_name=name,
                    tool_input=input_,
                    tool_id=block.get("id"),
                    raw=block,
                )


# ---------------------------------------------------------------------------
# Sub-agent helpers
# ---------------------------------------------------------------------------

@dataclass
class SubagentSummary:
    """Just enough metadata to display a sub-agent entry in the parent's UI."""

    filename: str               # bare filename, e.g. "agent-a142b619.jsonl"
    short_id: str               # "a142b619..." (everything after "agent-")
    first_prompt: str | None    # first user prompt the sub-agent received
    n_messages: int


def summarise_subagents(session_id: str) -> list[SubagentSummary]:
    summaries = []
    for p in find_subagent_files(session_id):
        entries = parse_transcript(p)
        first_prompt = next(
            (e.text for e in entries if e.kind == "user_text" and e.text), None
        )
        short_id = p.stem.removeprefix("agent-")
        summaries.append(SubagentSummary(
            filename=p.name,
            short_id=short_id,
            first_prompt=first_prompt[:200] if first_prompt else None,
            n_messages=len(entries),
        ))
    return summaries
