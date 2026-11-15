"""Health, the agent catalog and app settings."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Form

from bran.agents import list_agents
from bran.config import SETTINGS
from bran.health import health_snapshot
from bran.persistence import get_setting, set_setting

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    """Login, integrations, limits, slots and spend — the health strip."""
    return health_snapshot()


def _list_slash_commands() -> list[dict[str, str]]:
    """Discover slash commands from `.claude/commands/*.md` for autocomplete."""
    out: list[dict[str, str]] = []
    cmd_dir = SETTINGS.claude_dir / "commands"
    if not cmd_dir.exists():
        return out
    for p in sorted(cmd_dir.glob("*.md")):
        desc = ""
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if m:
            desc = m.group(1).strip().strip("\"'")
        else:
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith(("---", "name:", "argument-hint:", "allowed-tools:")):
                    desc = line[:120]
                    break
        out.append({"name": p.stem, "description": desc})
    return out


def agents_list() -> list[dict[str, Any]]:
    """The agent roster as both API surfaces present it."""
    return [
        {"name": a.name, "description": a.description, "model": a.model,
         "tools": a.tools, "subagents": list(a.subagents), "file": a.source}
        for a in list_agents()
    ]


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    """Agents + slash commands for the chat composer's autocomplete."""
    return {
        "agents": [{"name": a.name, "description": a.description} for a in list_agents()],
        "commands": _list_slash_commands(),
    }


@router.get("/agents")
def agents() -> list[dict[str, Any]]:
    return agents_list()


# The global "About me" instructions (every agent run sees them).

@router.get("/settings")
def app_settings() -> dict[str, Any]:
    return {"user_instructions": get_setting("user_instructions")}


@router.post("/settings")
def save_app_settings(
    user_instructions: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    set_setting("user_instructions", user_instructions)
    return {"user_instructions": user_instructions}
