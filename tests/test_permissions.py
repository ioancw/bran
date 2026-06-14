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


# ---------------------------------------------------------------------------
# Project working folder (projects.work_dir) — the Cowork-style read+write root
# ---------------------------------------------------------------------------

import pytest

from bran.background import current_project_id
from bran.permissions import allowed_write_target, validate_work_dir
from bran.persistence import ProjectRecord, insert_project


def _project_with_work_dir(work_dir: str) -> ProjectRecord:
    proj = ProjectRecord.new(name="wd-test")
    proj.work_dir = work_dir
    insert_project(proj)
    return proj


def test_validate_work_dir_rules(tmp_path):
    # Good: absolute existing dir → resolved.
    assert validate_work_dir(str(tmp_path)) == tmp_path.resolve()
    # Relative paths refused.
    with pytest.raises(ValueError):
        validate_work_dir("relative/dir")
    # Nonexistent dir refused.
    with pytest.raises(ValueError):
        validate_work_dir(str(tmp_path / "missing"))
    # Filesystem root refused — would grant write-everything.
    with pytest.raises(ValueError):
        validate_work_dir(str(Path(tmp_path.anchor)))
    # Empty refused.
    with pytest.raises(ValueError):
        validate_work_dir("   ")


def test_ambient_work_dir_admits_writes(tmp_path):
    proj = _project_with_work_dir(str(tmp_path))
    token = current_project_id.set(proj.id)
    try:
        inside = str(tmp_path / "report.md")
        assert _run(confine_writes_hook, "Write", {"file_path": inside}) == {}
        # Confinement still holds outside the working folder.
        outside = str(tmp_path.parent / "nope.txt")
        result = _run(confine_writes_hook, "Write", {"file_path": outside})
        assert _is_deny(result)
        # The deny message steers the agent at the working folder, not briefings.
        assert str(tmp_path.resolve()) in result["hookSpecificOutput"]["permissionDecisionReason"]
    finally:
        current_project_id.reset(token)


def test_work_dir_closed_without_ambient_project(tmp_path):
    # Same path, but no run context pointing at the project → still denied.
    _project_with_work_dir(str(tmp_path))
    inside = str(tmp_path / "report.md")
    assert _is_deny(_run(confine_writes_hook, "Write", {"file_path": inside}))


def test_invalid_stored_work_dir_grants_nothing(tmp_path):
    # A work_dir that no longer validates (folder deleted) fails closed.
    gone = tmp_path / "gone"
    gone.mkdir()
    proj = _project_with_work_dir(str(gone))
    gone.rmdir()
    token = current_project_id.set(proj.id)
    try:
        assert _is_deny(_run(confine_writes_hook, "Write", {"file_path": str(gone / "x.md")}))
    finally:
        current_project_id.reset(token)


def test_allowed_write_target_respects_ambient_roots(tmp_path):
    proj = _project_with_work_dir(str(tmp_path))
    target = str(tmp_path / "out.csv")
    # Without context: not sanctioned.
    assert allowed_write_target(target) is None
    # With the project ambient: resolved path comes back.
    token = current_project_id.set(proj.id)
    try:
        assert allowed_write_target(target) == (tmp_path / "out.csv").resolve()
    finally:
        current_project_id.reset(token)


# ---------------------------------------------------------------------------
# Artifact capture — runs record the files they produced
# ---------------------------------------------------------------------------

def test_absorb_message_records_sanctioned_writes_as_artifacts(tmp_path):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    from bran.persistence import RunRecord, insert_run, list_artifacts
    from bran.runner import _absorb_message

    proj = _project_with_work_dir(str(tmp_path))
    record = RunRecord.new(agent="research", task="produce a file", project_id=proj.id)
    insert_run(record)

    inside = str(tmp_path / "summary.md")
    outside = str(tmp_path.parent / "evil.md")  # hook would have denied this
    briefing = str(SETTINGS.briefings_dir / "briefing.md")
    msg = AssistantMessage(
        content=[
            ToolUseBlock(id="t1", name="Write", input={"file_path": inside}),
            ToolUseBlock(id="t2", name="Write", input={"file_path": outside}),
            ToolUseBlock(id="t3", name="Edit", input={"file_path": briefing}),
            ToolUseBlock(id="t4", name="Write", input={"file_path": inside}),  # dupe
        ],
        model="test",
    )
    token = current_project_id.set(proj.id)
    try:
        _absorb_message(record, msg)
    finally:
        current_project_id.reset(token)

    artifacts = list_artifacts(record.id)
    assert sorted(artifacts) == sorted([
        str((tmp_path / "summary.md").resolve()),
        str(Path(briefing).resolve()),
    ])
