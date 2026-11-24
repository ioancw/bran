"""The user's watchlist: the few instruments, themes and events they want
watched. Plain text, one item per line, kept in bran's settings.

`get_watchlist` is what the watchlist-monitor agent reads at the start of each
run; `save_watchlist` lets the agent you chat with change it when you ask
("add gold above 4,000 to my watchlist"). Served by the `bran_data` server.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

WATCHLIST_KEY = "watchlist"
MAX_CHARS = 4000
MAX_LINES = 40

EMPTY = (
    "There is no watchlist yet, so there is nothing to watch. The user sets one by telling bran "
    "in chat what to watch, one item per line, for example:\n"
    "  USD/JPY above 162\n  Brent below 65\n  US 10y yield above 5\n"
    "  Theme: BoJ intervention\n  BoE decisions"
)


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}], "is_error": True}


def read_watchlist() -> str:
    from bran.persistence import get_setting

    return (get_setting(WATCHLIST_KEY) or "").strip()


def clean(text: str) -> str:
    """One item per line, without bullets or blank lines."""
    lines = [ln.strip().lstrip("-*•").strip() for ln in (text or "").splitlines()]
    return "\n".join(ln for ln in lines if ln and not ln.startswith("#"))


@tool(
    "get_watchlist",
    (
        "Read the user's watchlist: the instruments (with thresholds), themes and events they "
        "want watched, one per line. Returns a note saying so when none has been set."
    ),
    {"type": "object", "properties": {}},
)
async def get_watchlist(args: dict[str, Any]) -> dict[str, Any]:
    text = read_watchlist()
    return _ok("The watchlist:\n" + text if text else EMPTY)


@tool(
    "save_watchlist",
    (
        "Replace the user's watchlist with `text`: one item per line, e.g. 'USD/JPY above 162', "
        "'Brent below 65', 'Theme: BoJ intervention', 'BoE decisions'. Only call this when the "
        "user asks to set or change what is watched. It REPLACES the whole list, so read it "
        "first with get_watchlist and send back every line that should remain. An empty text "
        "clears it. Tell the user the list as saved."
    ),
    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
)
async def save_watchlist(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import set_setting

    text = clean(str(args.get("text") or ""))
    if len(text) > MAX_CHARS or text.count("\n") + 1 > MAX_LINES:
        return _err(
            f"a watchlist is at most {MAX_LINES} lines. A long list stops being watched closely; "
            "ask the user which items matter most."
        )
    set_setting(WATCHLIST_KEY, text)
    return _ok("Saved. The watchlist is now:\n" + text if text else "The watchlist is now empty.")


WATCHLIST_TOOLS = [get_watchlist, save_watchlist]
