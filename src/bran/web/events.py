"""One normalized chat-event schema for the SPA — live stream and replay alike.

The legacy HTMX UI renders a conversation two different ways: `_serialize_message`
shapes the live SSE stream, while `chat_history` hand-maps transcript `Entry`s for
replay-on-refresh — two subtly different wire shapes the client has to special-case
(`ev.type || ev.kind`). That drift is the thing the Svelte frontend kills.

Here both sources funnel through one schema. Every event is a flat dict with a
`type` discriminator and type-specific fields:

    {"type": "session",     "session_id": str}
    {"type": "routed",      "agent": str}
    {"type": "user",        "text": str, "ts"?: str}          # replay only
    {"type": "text",        "text": str, "ts"?: str}          # assistant prose;
                                                              #   consecutive ones
                                                              #   merge into one bubble
                                                              #   (ts = ISO-8601 UTC,
                                                              #   present when known)
    {"type": "thinking",    "text": str}
    {"type": "tool_use",    "name": str, "input": dict, "tool_id": str|None}
                                                              # delegation folded in:
                                                              #   name in {Agent,Task},
                                                              #   input has subagent_type
    {"type": "tool_result", "tool_id": str|None, "text": str, "is_error": bool}
    {"type": "result",      "num_turns": int|None, "total_cost_usd": float|None,
                            "session_id": str|None}
    {"type": "error",       "message": str}                   # stream only
    {"type": "done"}                                          # stream only

`events_from_message` (live) and `events_from_transcript_entry` (replay) emit this
exact shape, so the client renders both through a single code path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bran.transcript import Entry

# Per-run stored events (runs.metadata["events"]). Tool results are the bulk
# of a transcript; cap each so a chatty run can't balloon the SQLite row, and
# cap the whole list so the UI replay stays snappy.
STORED_RESULT_CHARS = 8_000
STORED_EVENTS_MAX_CHARS = 1_500_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_ts(ev: dict[str, Any], ts: str | None) -> dict[str, Any]:
    """Attach a timestamp when one is known; the key is simply absent otherwise."""
    if ts:
        ev["ts"] = ts
    return ev


def compact_for_storage(ev: dict[str, Any]) -> dict[str, Any]:
    """Trim one live event for persistence in the run row."""
    if ev.get("type") == "tool_result":
        text = ev.get("text") or ""
        if len(text) > STORED_RESULT_CHARS:
            ev = {**ev, "text": text[:STORED_RESULT_CHARS] + "\n[… result truncated …]"}
    return ev


def events_from_runs(runs: list[Any]) -> list[dict[str, Any]]:
    """Rebuild a conversation from its chat-turn run rows when the SDK's
    transcript file is gone (Claude Code prunes sessions after ~30 days).

    Each turn becomes the user's prompt followed by the turn's stored events
    when the run kept them (runs.metadata["events"], recorded live), else the
    final reply text — or the error for a failed turn. Lower fidelity than the
    transcript (no tool calls for pre-storage turns) but the conversation
    itself survives.
    """
    out: list[dict[str, Any]] = []
    for r in sorted(runs, key=lambda x: x.started_at or ""):
        out.append(_with_ts({"type": "user", "text": r.task or ""}, r.started_at))
        stored = (r.metadata or {}).get("events")
        if stored:
            out.extend(e for e in stored if e.get("type") not in ("session", "done"))
            continue
        if r.result:
            out.append(_with_ts({"type": "text", "text": r.result}, r.ended_at or r.started_at))
        if r.status == "failed":
            out.append({"type": "error", "message": r.error or "Run failed"})
        elif r.status == "cancelled":
            out.append({"type": "error", "message": r.error or "Run cancelled"})
    return out


def _text_from_tool_result_content(content: Any) -> str:
    """tool_result content is either a string or a list of content blocks."""
    if isinstance(content, list):
        parts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return "\n".join(parts)
    return str(content or "")


def events_from_message(message: Any) -> list[dict[str, Any]]:
    """Convert one live SDK message into zero or more normalized events.

    Uses class-name checks (rather than isinstance) so this module stays free of
    a hard SDK import and tolerates the SDK's occasional type renames.
    """
    mtype = type(message).__name__

    if mtype == "SystemMessage" and getattr(message, "subtype", None) == "init":
        sid = (getattr(message, "data", None) or {}).get("session_id")
        return [{"type": "session", "session_id": sid}] if sid else []

    if mtype == "AssistantMessage":
        out: list[dict[str, Any]] = []
        for block in getattr(message, "content", None) or []:
            btype = type(block).__name__
            if btype == "TextBlock":
                text = getattr(block, "text", "")
                if text:
                    out.append({"type": "text", "text": text, "ts": _now_iso()})
            elif btype == "ThinkingBlock":
                out.append({"type": "thinking", "text": getattr(block, "thinking", "")})
            elif btype == "ToolUseBlock":
                out.append({
                    "type": "tool_use",
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", None) or {},
                    "tool_id": getattr(block, "id", None),
                })
        return out

    if mtype == "UserMessage":
        # User messages in the stream are tool results echoed back. The SDK hands
        # these as dicts, not typed blocks.
        out = []
        content = (
            getattr(getattr(message, "message", None), "content", None)
            or getattr(message, "content", None)
        )
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append({
                        "type": "tool_result",
                        "tool_id": block.get("tool_use_id"),
                        "is_error": bool(block.get("is_error")),
                        "text": _text_from_tool_result_content(block.get("content")),
                    })
        return out

    if mtype == "ResultMessage":
        return [{
            "type": "result",
            "num_turns": getattr(message, "num_turns", None),
            "total_cost_usd": getattr(message, "total_cost_usd", None),
            "session_id": getattr(message, "session_id", None),
        }]

    return []


def events_from_transcript_entry(entry: Entry) -> list[dict[str, Any]]:
    """Convert one replayed transcript `Entry` into the same normalized events."""
    k = entry.kind
    if k == "user_text":
        return [_with_ts({"type": "user", "text": entry.text or ""}, entry.timestamp)]
    if k == "assistant_text":
        return [_with_ts({"type": "text", "text": entry.text or ""}, entry.timestamp)]
    if k == "thinking":
        return [{"type": "thinking", "text": entry.text or ""}]
    if k == "tool_call":
        return [{
            "type": "tool_use",
            "name": entry.tool_name or "",
            "input": entry.tool_input or {},
            "tool_id": entry.tool_id,
        }]
    if k == "delegation":
        # Delegation is just an Agent/Task tool call; fold it into tool_use so the
        # client has one render path (input carries subagent_type + prompt).
        return [{
            "type": "tool_use",
            "name": entry.tool_name or "Agent",
            "input": entry.tool_input or {},
            "tool_id": entry.tool_id,
        }]
    if k == "tool_result":
        return [{
            "type": "tool_result",
            "tool_id": entry.tool_id,
            "is_error": bool(entry.tool_is_error),
            "text": entry.text or "",
        }]
    return []
