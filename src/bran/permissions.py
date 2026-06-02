"""Filesystem-write confinement for agents that ingest untrusted input.

Web-facing agents (`research`, `finance-news`) fetch arbitrary internet content
*and* hold the `Write` tool under `permission_mode="acceptEdits"`, which
auto-approves edits. A prompt-injection payload buried in a fetched page could
therefore steer an agent into overwriting source, configs, or the user's files
anywhere under the project root.

We install a PreToolUse hook that *denies* any file-writing tool call whose
target resolves outside bran's own home directory. Returning an empty dict for
everything else defers to the normal permission flow, so legitimate output
(e.g. the finance agent's briefings under `.bran/briefings/`) is unaffected —
we only ever intervene to block an out-of-sandbox write.

Why a hook and not the `can_use_tool` callback: the callback requires the SDK's
streaming-mode prompt (an AsyncIterable), but bran's runner submits a plain
string via `query()`. PreToolUse hooks work on both paths and also fire inside
delegated sub-agents, so one hook on the orchestrator covers the agents it
delegates to as well.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bran.config import SETTINGS

# Tool-input keys that may carry a write target path (across Write/Edit/Notebook).
_PATH_KEYS = ("file_path", "notebook_path")

# HookMatcher pattern: only these tools trigger the hook. (Bash could also write,
# but no bran agent grants Bash; revisit this matcher if that changes.)
WRITE_TOOL_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"


def _default_roots() -> list[Path]:
    """Directories a confined agent is allowed to write into.

    `bran_home` contains the briefings dir. We also include `project_root/.bran`
    so the finance agent's hard-coded relative `.bran/briefings/...` target
    resolves as allowed even though it's written relative to the cwd; with the
    default BRAN_HOME the two coincide.
    """
    roots = {SETTINGS.bran_home.resolve(), (SETTINGS.project_root / ".bran").resolve()}
    return list(roots)


def _resolve(path_str: str) -> Path | None:
    """Resolve a tool's target path (relative paths are relative to project_root).

    `.resolve()` normalises `..` and follows symlinks, which is what closes the
    traversal escape — a `../../etc/passwd` target resolves to its real location
    and then fails the containment check.
    """
    if not path_str:
        return None
    p = Path(path_str)
    if not p.is_absolute():
        p = SETTINGS.project_root / p
    try:
        return p.resolve()
    except OSError:
        return None


def _is_within(target: Path, roots: list[Path]) -> bool:
    return any(target == r or r in target.parents for r in roots)


def make_write_confinement_hook(roots: list[Path] | None = None):
    """Build a PreToolUse hook denying writes outside `roots`.

    `roots=None` resolves the default bran dirs lazily on each call, so tests
    that point SETTINGS at a temp dir are honoured.
    """

    async def hook(
        input: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        tool_input = input.get("tool_input") or {}
        target_str = ""
        for key in _PATH_KEYS:
            if tool_input.get(key):
                target_str = tool_input[key]
                break
        if not target_str:
            # No path to police (or not a write we recognise) — defer.
            return {}

        active_roots = roots if roots is not None else _default_roots()
        target = _resolve(target_str)
        if target is not None and _is_within(target, active_roots):
            return {}  # inside the sandbox → let the normal flow accept it

        pretty = ", ".join(str(r) for r in active_roots)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Write to {target_str!r} blocked: this agent may only write "
                    f"inside {pretty}. Save any output under the briefings "
                    f"directory ({SETTINGS.briefings_dir}) instead."
                ),
            }
        }

    return hook


# Default hook bound to bran's home dirs, reused across agents.
confine_writes_hook = make_write_confinement_hook()
