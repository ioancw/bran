"""Write-confinement hook: deny writes outside the allowed roots, defer the rest."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bran.config import SETTINGS
from bran.permissions import (
    WRITE_TOOL_MATCHER,
    _is_within,
    _resolve,
    confine_writes_hook,
    make_write_confinement_hook,
)


def _run(hook, tool_name: str, tool_input: dict) -> dict:
    return asyncio.run(hook({"tool_name": tool_name, "tool_input": tool_input}, "tid", {}))


def _is_deny(result: dict) -> bool:
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def test_matcher_targets_write_tools():
    assert WRITE_TOOL_MATCHER == "Write|Edit|MultiEdit|NotebookEdit"


def test_write_inside_briefings_is_allowed():
    target = str(SETTINGS.briefings_dir / "briefing_2026-05-28.md")
    result = _run(confine_writes_hook, "Write", {"file_path": target})
    assert result == {}  # empty → defer to normal (acceptEdits) flow


def test_write_inside_bran_home_is_allowed():
    target = str(SETTINGS.bran_home / "scratch.txt")
    assert _run(confine_writes_hook, "Write", {"file_path": target}) == {}


def test_write_outside_sandbox_is_denied():
    # An absolute path well outside bran_home.
    outside = str(Path(SETTINGS.project_root).anchor or "/") + "tmp_evil_bran_test.txt"
    result = _run(confine_writes_hook, "Write", {"file_path": outside})
    assert _is_deny(result)
    assert "blocked" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_path_traversal_escape_is_denied():
    # Starts under the sandbox but climbs out via `..`.
    sneaky = str(SETTINGS.bran_home / ".." / ".." / "secrets.txt")
    result = _run(confine_writes_hook, "Write", {"file_path": sneaky})
    assert _is_deny(result)


def test_source_file_write_is_denied():
    # The exact attack we care about: overwriting project source.
    target = str(SETTINGS.project_root / "src" / "bran" / "config.py")
    assert _is_deny(_run(confine_writes_hook, "Write", {"file_path": target}))


def test_notebook_edit_path_key_is_checked():
    outside = str(SETTINGS.project_root / "evil.ipynb")
    assert _is_deny(_run(confine_writes_hook, "NotebookEdit", {"notebook_path": outside}))


def test_missing_path_defers():
    # No recognised path key → nothing to police, defer to normal flow.
    assert _run(confine_writes_hook, "Write", {}) == {}


def test_custom_roots_are_honoured(tmp_path):
    hook = make_write_confinement_hook(roots=[tmp_path.resolve()])
    inside = str(tmp_path / "ok.txt")
    outside = str(tmp_path.parent / "nope.txt")
    assert _run(hook, "Write", {"file_path": inside}) == {}
    assert _is_deny(_run(hook, "Write", {"file_path": outside}))


def test_resolve_and_within_helpers(tmp_path):
    root = tmp_path.resolve()
    assert _is_within(_resolve(str(tmp_path / "a" / "b.txt")), [root])
    assert not _is_within(_resolve(str(tmp_path.parent / "x.txt")), [root])
