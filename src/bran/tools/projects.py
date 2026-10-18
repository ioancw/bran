"""Project tools for the `bran` MCP server.

Tools:
    - get_project_activity: one call that returns what has happened in a
      project lately (chats, runs, pinned facts) — the raw material for a
      project summary.
    - save_project_summary: store the summary the agent wrote, so the project
      page shows it and every future chat in the project starts from it.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

_RESULT_EXCERPT = 700
_MAX_ITEMS = 30


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _excerpt(text: str | None, n: int = _RESULT_EXCERPT) -> str:
    t = " ".join((text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def project_activity_digest(project_id: str) -> str | None:
    """The plain-text digest the summariser reads: the project's brief and
    pinned facts, its recent chats (title + the last reply), and its recent
    background runs (task + result excerpt). None when the project is unknown."""
    from bran.persistence import get_project, list_chats, list_project_memories, list_runs

    project = get_project(project_id)
    if project is None:
        return None
    parts = [f"# Project: {project.name}"]
    if project.description.strip():
        parts.append(project.description.strip())
    if project.instructions.strip():
        parts.append("## Instructions\n" + project.instructions.strip())
    memories = list_project_memories(project_id)
    if memories:
        parts.append("## Pinned facts\n" + "\n".join(f"- {m.text}" for m in memories))
    if project.summary.strip():
        parts.append(f"## Previous summary (written {project.summary_at or 'earlier'})\n" + project.summary.strip())

    chats = list_chats(limit=_MAX_ITEMS, project_id=project_id)
    if chats:
        lines = []
        for c in chats:
            turns = [r for r in list_runs(session_id=c.id, limit=50) if r.source in ("chat", "synthesis")]
            last = next((r for r in turns if r.result), None)
            line = f"- {c.updated_at[:10]} · {c.title}"
            if last is not None:
                line += f"\n  Last reply: {_excerpt(last.result)}"
            lines.append(line)
        parts.append("## Recent chats (newest first)\n" + "\n".join(lines))

    runs = [r for r in list_runs(project_id=project_id, limit=_MAX_ITEMS, exclude_chats=True)
            if r.source != "subagent"]
    if runs:
        lines = []
        for r in runs:
            line = f"- {r.started_at[:10]} · {r.agent} ({r.source}, {r.status}) — {_excerpt(r.task, 160)}"
            if r.result:
                line += f"\n  Result: {_excerpt(r.result)}"
            elif r.error:
                line += f"\n  Error: {_excerpt(r.error, 200)}"
            lines.append(line)
        parts.append("## Recent background runs (newest first)\n" + "\n".join(lines))
    if not chats and not runs:
        parts.append("No chats or runs yet.")
    return "\n\n".join(parts)


@tool(
    "get_project_activity",
    (
        "Everything that has happened in a bran project lately, in one call: its "
        "brief and pinned facts, the previous summary if any, recent chats with "
        "their last replies, and recent background runs with result excerpts. "
        "Use it before writing or refreshing a project summary. The project_id "
        "is given in your task or system prompt — never invent one."
    ),
    {"project_id": str},
)
async def get_project_activity(args: dict[str, Any]) -> dict[str, Any]:
    digest = project_activity_digest(str(args.get("project_id") or "").strip())
    if digest is None:
        return _ok("Error: no such project.")
    return _ok(digest)


@tool(
    "save_project_summary",
    (
        "Save the project summary you wrote. It replaces the previous summary, "
        "is shown on the project page, and is added to the system prompt of every "
        "future chat in the project — so write it for an agent arriving cold: what "
        "the project is about, what has been done recently, what is open. Markdown, "
        "150–300 words."
    ),
    {"project_id": str, "text": str},
)
async def save_project_summary(args: dict[str, Any]) -> dict[str, Any]:
    from bran.persistence import get_project, set_project_summary

    project_id = str(args.get("project_id") or "").strip()
    text = (args.get("text") or "").strip()
    if not text:
        return _ok("Error: empty summary — nothing saved.")
    project = get_project(project_id)
    if project is None:
        return _ok("Error: no such project.")
    set_project_summary(project_id, text)
    return _ok(f"Saved the summary for project '{project.name}'.")


PROJECT_TOOLS = [get_project_activity, save_project_summary]
