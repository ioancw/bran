"""The unified event schema: live messages and replay entries emit one shape."""

from __future__ import annotations

from types import SimpleNamespace

from bran.transcript import Entry
from bran.web.events import events_from_message, events_from_transcript_entry


# --- Stand-ins whose class __name__ matches the SDK types events.py keys off. ---

class TextBlock:
    def __init__(self, text): self.text = text

class ThinkingBlock:
    def __init__(self, thinking): self.thinking = thinking

class ToolUseBlock:
    def __init__(self, name, input, id): self.name = name; self.input = input; self.id = id

class AssistantMessage:
    def __init__(self, content): self.content = content

class SystemMessage:
    def __init__(self, subtype, data): self.subtype = subtype; self.data = data

class ResultMessage:
    def __init__(self, num_turns, total_cost_usd, session_id):
        self.num_turns = num_turns
        self.total_cost_usd = total_cost_usd
        self.session_id = session_id

class UserMessage:
    def __init__(self, content): self.content = content


def test_system_init_yields_session():
    msg = SystemMessage("init", {"session_id": "sess-1"})
    assert events_from_message(msg) == [{"type": "session", "session_id": "sess-1"}]


def test_system_init_without_session_yields_nothing():
    assert events_from_message(SystemMessage("init", {})) == []


def test_assistant_message_explodes_blocks_in_order():
    msg = AssistantMessage([
        TextBlock("hello "),
        ToolUseBlock("Read", {"file_path": "/x"}, "t1"),
        ThinkingBlock("hmm"),
        TextBlock(""),  # empty text is dropped
    ])
    evs = events_from_message(msg)
    assert evs == [
        {"type": "text", "text": "hello "},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}, "tool_id": "t1"},
        {"type": "thinking", "text": "hmm"},
    ]


def test_user_message_tool_results():
    msg = UserMessage([
        {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
         "content": [{"type": "text", "text": "ok"}]},
        {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "boom"},
    ])
    assert events_from_message(msg) == [
        {"type": "tool_result", "tool_id": "t1", "is_error": False, "text": "ok"},
        {"type": "tool_result", "tool_id": "t2", "is_error": True, "text": "boom"},
    ]


def test_result_message():
    msg = ResultMessage(num_turns=3, total_cost_usd=0.12, session_id="sess-1")
    assert events_from_message(msg) == [{
        "type": "result", "num_turns": 3, "total_cost_usd": 0.12, "session_id": "sess-1",
    }]


def test_unknown_message_is_ignored():
    assert events_from_message(SimpleNamespace()) == []


# --- Transcript replay produces the SAME shapes the live stream does. ---

def test_transcript_text_matches_live_text_shape():
    e = Entry(kind="assistant_text", text="hello")
    assert events_from_transcript_entry(e) == [{"type": "text", "text": "hello"}]


def test_transcript_tool_call_matches_live_tool_use_shape():
    e = Entry(kind="tool_call", tool_name="Grep", tool_input={"pattern": "x"}, tool_id="t9")
    assert events_from_transcript_entry(e) == [
        {"type": "tool_use", "name": "Grep", "input": {"pattern": "x"}, "tool_id": "t9"},
    ]


def test_transcript_delegation_folds_into_tool_use():
    e = Entry(kind="delegation", tool_name="Agent",
              tool_input={"subagent_type": "research", "prompt": "go"}, tool_id="t3")
    assert events_from_transcript_entry(e) == [{
        "type": "tool_use", "name": "Agent",
        "input": {"subagent_type": "research", "prompt": "go"}, "tool_id": "t3",
    }]


def test_transcript_user_and_tool_result():
    assert events_from_transcript_entry(Entry(kind="user_text", text="hi")) == [
        {"type": "user", "text": "hi"}
    ]
    e = Entry(kind="tool_result", tool_id="t1", tool_is_error=True, text="err")
    assert events_from_transcript_entry(e) == [
        {"type": "tool_result", "tool_id": "t1", "is_error": True, "text": "err"}
    ]


def test_live_and_replay_text_events_are_identical():
    live = events_from_message(AssistantMessage([TextBlock("same")]))
    replay = events_from_transcript_entry(Entry(kind="assistant_text", text="same"))
    assert live == replay
