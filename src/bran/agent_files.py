"""Agents as files.

Every bran agent is one markdown file in `agents/`: YAML-style frontmatter
for the configuration, the body for the system prompt. The same format as a
Claude Code subagent file, plus a few bran keys, so one file is the single
source of truth for an agent in BOTH of its forms — the top-level agent you
can chat with, schedule and spawn, and the inline subagent another agent may
delegate to through the Agent tool.

    ---
    name: research                      # defaults to the file stem
    description: Web research specialist. Use it for …
    model: sonnet                       # alias or full id; "default" = BRAN_DEFAULT_MODEL
    tools: [WebSearch, WebFetch, Read, Write, mcp__bran_docs__read_pdf, mcp__tavily__*]
    max_turns: 40
    subagents: [research, summariser]   # other agent files this one may delegate to
    ---
    You are a careful web research analyst. …

Keys: `name`, `description` (required), `model`, `tools` (list, or a
comma-separated string as Claude Code writes it), `max_turns`, `subagents`,
`permission_mode`, `skills`. `mcp__<server>__*` expands to every tool of that
server; MCP servers an agent needs are derived from the tool names, never
declared. The prompt may use `{{briefings_dir}}` and `{{roster}}` (a bullet
list of the agent's subagents with their descriptions).

The frontmatter parser is deliberately small (flat keys, scalars, inline and
block lists) so bran has no YAML dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AgentFileError(ValueError):
    """A malformed agent file. The message names the file and the problem."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    prompt: str
    tools: list[str]
    model: str | None
    max_turns: int | None
    subagents: list[str]
    permission_mode: str
    skills: list[str]
    path: Path | None


_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)\Z", re.S)
_INT = re.compile(r"^-?\d+$")


def _scalar(raw: str) -> Any:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if _INT.match(s):
        return int(s)
    return s


def _split_commas(s: str) -> list[str]:
    return [p for p in (x.strip() for x in s.split(",")) if p]


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a file into (frontmatter dict, body). Supports `key: scalar`,
    `key: [a, b]`, and `key:` followed by `  - item` lines."""
    m = _FRONTMATTER.match(text.lstrip("﻿"))
    if not m:
        raise AgentFileError("missing frontmatter (a `---` block at the top of the file)")
    meta: dict[str, Any] = {}
    key: str | None = None
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0] in " \t":
            item = raw.strip()
            if item.startswith("- ") and key is not None and isinstance(meta.get(key), list):
                meta[key].append(_scalar(item[2:]))
                continue
            raise AgentFileError(f"unexpected indented line in frontmatter: {raw.strip()!r}")
        if ":" not in raw:
            raise AgentFileError(f"expected `key: value`, got {raw.strip()!r}")
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            meta[key] = []  # a block list follows (or the key is empty)
        elif val.startswith("[") and val.endswith("]"):
            meta[key] = [_scalar(x) for x in _split_commas(val[1:-1])]
        else:
            meta[key] = _scalar(val)
    return meta, m.group(2).strip()


def _as_list(value: Any, key: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return _split_commas(value)
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    raise AgentFileError(f"`{key}` must be a list or a comma-separated string")


def spec_from_text(text: str, *, default_name: str, path: Path | None = None) -> AgentSpec:
    meta, body = parse_frontmatter(text)
    name = str(meta.get("name") or default_name).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise AgentFileError(f"invalid agent name {name!r}")
    description = str(meta.get("description") or "").strip()
    if not description:
        raise AgentFileError("`description` is required — it tells Claude when to use the agent")
    if not body:
        raise AgentFileError("the body (system prompt) is empty")
    max_turns = meta.get("max_turns")
    if max_turns is not None and max_turns != "" and not isinstance(max_turns, int):
        raise AgentFileError("`max_turns` must be an integer")
    model = meta.get("model")
    return AgentSpec(
        name=name,
        description=description,
        prompt=body,
        tools=_as_list(meta.get("tools"), "tools"),
        model=(str(model).strip() or None) if model not in (None, "") else None,
        max_turns=max_turns if isinstance(max_turns, int) else None,
        subagents=_as_list(meta.get("subagents"), "subagents"),
        permission_mode=str(meta.get("permission_mode") or "dontAsk"),
        skills=_as_list(meta.get("skills"), "skills"),
        path=path,
    )


def load_agent_dir(directory: Path) -> list[AgentSpec]:
    """Every `*.md` in `directory`, parsed. Raises AgentFileError naming the
    offending file; a missing directory simply yields no agents."""
    if not directory.is_dir():
        return []
    specs: list[AgentSpec] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.md")):
        try:
            spec = spec_from_text(path.read_text(encoding="utf-8"), default_name=path.stem, path=path)
        except AgentFileError as e:
            raise AgentFileError(f"{path.name}: {e}") from None
        if spec.name in seen:
            raise AgentFileError(f"{path.name}: duplicate agent name {spec.name!r} (also in {seen[spec.name].name})")
        seen[spec.name] = path
        specs.append(spec)
    return specs
