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

    On top of the static roots, the *ambient* bran project's working folder
    (projects.work_dir) is admitted when the current run is attached to one —
    that's the Cowork-style "point a project at a real folder and let the agent
    produce files there". The run's project travels in a contextvar set by
    runner._drive, which propagates into SDK hook callbacks the same way it
    already does into in-process MCP tools (see spawn_agent).
    """
    from bran.background import current_project_id

    return write_roots_for_project(current_project_id.get())


def write_roots_for_project(project_id: str | None) -> list[Path]:
    """The write roots for a run attached to `project_id` (None = static only).

    Also used at artifact-serving time, where the project comes from the run
    record rather than the ambient contextvar. Lazy imports keep module load
    light and dodge import cycles (permissions is imported by agents at
    startup; persistence is heavier).
    """
    roots = {SETTINGS.bran_home.resolve(), (SETTINGS.project_root / ".bran").resolve()}
    wd = work_dir_for_project(project_id)
    if wd is not None:
        roots.add(wd)
    return list(roots)


def work_dir_for_project(project_id: str | None) -> Path | None:
    """The validated work_dir of a project, or None if unset/invalid."""
    if not project_id:
        return None
    from bran.persistence import get_project

    project = get_project(project_id)
    if project is None or not (project.work_dir or "").strip():
        return None
    try:
        return validate_work_dir(project.work_dir)
    except ValueError:
        # A stored work_dir that no longer validates (deleted folder, etc.)
        # simply grants nothing — fail closed.
        return None


def ambient_work_dir() -> Path | None:
    """The validated work_dir of the currently-executing run's project, if any."""
    from bran.background import current_project_id

    return work_dir_for_project(current_project_id.get())


def validate_work_dir(raw: str) -> Path:
    """Validate a user-supplied working-folder path; returns it resolved.

    Must be an absolute path to an existing directory, and not a filesystem
    root — a work_dir of `/` or `C:\\` would hand the agent write access to
    everything, which defeats the confinement entirely.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty path")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ValueError(f"work_dir must be an absolute path, got {raw!r}")
    p = p.resolve()
    if str(p) == p.anchor:
        raise ValueError("work_dir may not be a filesystem root")
    if not p.is_dir():
        raise ValueError(f"work_dir does not exist or is not a directory: {p}")
    return p


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


def allowed_write_target(path_str: str) -> Path | None:
    """Resolve `path_str` and return it iff it falls inside the current allowed
    write roots (incl. the ambient project's work_dir), else None.

    Shared by the confinement hook's logic and by artifact capture/serving in
    the runner + API, so "what counts as a sanctioned write" has one definition.
    """
    target = _resolve(path_str)
    if target is not None and _is_within(target, _default_roots()):
        return target
    return None


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
        wd = ambient_work_dir()
        suggestion = (
            f"Save any output in the project's working folder ({wd}) instead."
            if wd is not None
            else f"Save any output under the briefings directory ({SETTINGS.briefings_dir}) instead."
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Write to {target_str!r} blocked: this agent may only write "
                    f"inside {pretty}. {suggestion}"
                ),
            }
        }

    return hook


# Default hook bound to bran's home dirs, reused across agents.
confine_writes_hook = make_write_confinement_hook()
