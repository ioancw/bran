"""Transcript parsing — golden tests against realistic SDK JSONL.

This drives the entire chat-history / run-transcript UI. The parser silently
skips unknown line types by design, so an SDK format change fails *quietly*
(chats render empty — the exact failure the owner hit once). These tests are
the canary: they assert real-shaped lines still produce the expected Entry
stream, and that the session-id charset guard blocks path-glob abuse.
"""

from __future__ import annotations

import json

import pytest

from bran.transcript import Entry, find_session_file, parse_transcript

# A realistic slice of a Claude Agent SDK transcript: user prompt, assistant
# thinking + text + a tool_use, the paired tool_result, an Agent delegation,
# and non-render lines (file-history-snapshot, an unknown future type) that
# must be skipped without breaking the stream.
_LINES = [
    {"type": "file-history-snapshot", "timestamp": "t0"},
    {
        "type": "user",
        "timestamp": "t1",
        "message": {"role": "user", "content": "summarise the FT front page"},
    },
    {
        "type": "assistant",
        "timestamp": "t2",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "I should fetch the feed first."},
                {"type": "text", "text": "On it — fetching now."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "mcp__bran_docs__fetch_url",
                    "input": {"url": "https://ft.com/rss"},
                },
            ],
        },
    },
    {
        "type": "user",
        "timestamp": "t3",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01",
                    "content": [{"type": "text", "text": "<rss>…</rss>"}],
                }
            ],
        },
    },
    {
        "type": "assistant",
        "timestamp": "t4",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_02",
                    "name": "Agent",
                    "input": {"subagent_type": "research", "prompt": "dig deeper"},
                }
            ],
        },
    },
    {"type": "some-future-type", "timestamp": "t5", "message": {"content": "ignore me"}},
]


@pytest.fixture()
def transcript_file(tmp_path):
    p = tmp_path / "sess.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for line in _LINES:
            f.write(json.dumps(line) + "\n")
    return p


def test_parse_full_timeline(transcript_file):
    entries = parse_transcript(transcript_file)
    kinds = [e.kind for e in entries]
    assert kinds == [
        "user_text",
        "thinking",
        "assistant_text",
        "tool_call",
        "tool_result",
        "delegation",
    ]


def test_parse_field_extraction(transcript_file):
    by_kind = {e.kind: e for e in parse_transcript(transcript_file)}
    assert by_kind["user_text"].text == "summarise the FT front page"
    assert by_kind["thinking"].text == "I should fetch the feed first."
    assert by_kind["tool_call"].tool_name == "mcp__bran_docs__fetch_url"
    assert by_kind["tool_call"].tool_input == {"url": "https://ft.com/rss"}
    tr = by_kind["tool_result"]
    assert tr.tool_id == "toolu_01" and tr.tool_is_error is False
    assert tr.text == "<rss>…</rss>"
    deleg = by_kind["delegation"]
    assert deleg.tool_name == "Agent" and deleg.subagent_type == "research"


def test_malformed_lines_are_skipped(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"type":"user","message":{"content":"hi"}}\n'
        "not json at all\n"
        "\n"
        '{"type":"assistant","message":{"content":[{"type":"text","text":"yo"}]}}\n',
        encoding="utf-8",
    )
    kinds = [e.kind for e in parse_transcript(p)]
    assert kinds == ["user_text", "assistant_text"]


def test_canary_nonempty(transcript_file):
    """If an SDK format change ever makes the parser drop everything, this
    fails loudly instead of the UI silently rendering empty."""
    assert len(parse_transcript(transcript_file)) >= 5


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "a/b", "*", "id with space", "", "..", "x.jsonl"],
)
def test_find_session_file_rejects_bad_ids(bad_id):
    # None (not an exception, not a filesystem hit) for anything outside the
    # session-id alphabet — the glob interpolation stays safe.
    assert find_session_file(bad_id) is None


def test_entry_is_dataclass():
    e = Entry(kind="user_text", text="x")
    assert e.kind == "user_text" and e.raw == {}
