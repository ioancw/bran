"""Cross-run context MCP tools: search what bran has already produced.

`search_runs` looks through past run results; `search_briefings` through the
saved briefings, section by section. Both return citations (a run id, or a
file and its date) so a claim can be traced. See `bran.search`.

Served by the `bran_data` server alongside the dataset tools:
`mcp__bran_data__search_runs`, `mcp__bran_data__search_briefings`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}], "is_error": True}


def _limit(value: Any) -> int:
    try:
        return max(1, min(50, int(value)))
    except (TypeError, ValueError):
        return 10


@tool(
    "search_runs",
    (
        "Search the results of PAST runs (briefings delivered, research done, earlier chat "
        "answers) for words or a \"quoted phrase\". Use it before redoing work, and to find what "
        "was said about something before. Optional: `agent` (e.g. 'finance-news'), `since` "
        "(YYYY-MM-DD), `limit` (default 10). Each hit gives the run id, date, agent and a "
        "snippet; read the whole result with get_run_result if you have that tool, and cite "
        "the run id and date when you rely on it."
    ),
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "agent": {"type": "string"},
            "since": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
)
async def search_runs(args: dict[str, Any]) -> dict[str, Any]:
    from bran.search import SearchError
    from bran.search import search_runs as run

    query = (args.get("query") or "").strip()
    try:
        hits = await asyncio.to_thread(
            run, query, agent=args.get("agent"), since=args.get("since"), limit=_limit(args.get("limit")),
        )
    except SearchError as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001
        return _err(f"the search could not be run: {e}")
    if not hits:
        return _ok(f"No past run mentions {query!r}.")
    lines = [
        f"- run {h['run_id']} · {h['date']} · {h['agent']} ({h['source']})\n  {h['snippet']}"
        for h in hits
    ]
    return _ok(f"{len(hits)} past run{'s' if len(hits) != 1 else ''} matching {query!r}:\n" + "\n".join(lines))


@tool(
    "search_briefings",
    (
        "Search the saved daily briefings for words or a \"quoted phrase\", section by section. "
        "Use it to answer 'what did the briefings say about X' and to see how a story developed "
        "over time. Optional: `since` and `until` (YYYY-MM-DD), `limit` (default 10). Each hit "
        "gives the briefing's date, the section, the file path and a snippet; Read the file for "
        "the full text, and cite the date when you rely on it. Briefings are built from news "
        "headlines, so treat a figure in one as reported, not checked."
    ),
    {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "since": {"type": "string"},
            "until": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
)
async def search_briefings(args: dict[str, Any]) -> dict[str, Any]:
    from bran.search import SearchError
    from bran.search import search_briefings as run

    query = (args.get("query") or "").strip()
    try:
        hits = await asyncio.to_thread(
            run, query, since=args.get("since"), until=args.get("until"), limit=_limit(args.get("limit")),
        )
    except SearchError as e:
        return _err(str(e))
    except Exception as e:  # noqa: BLE001
        return _err(f"the search could not be run: {e}")
    if not hits:
        return _ok(f"No briefing mentions {query!r}.")
    lines = [f"- {h['date']} · {h['section']} · {h['path']}\n  {h['snippet']}" for h in hits]
    return _ok(
        f"{len(hits)} briefing section{'s' if len(hits) != 1 else ''} matching {query!r}:\n" + "\n".join(lines)
    )


CONTEXT_TOOLS = [search_runs, search_briefings]
