"""Per-project working files — bran's managed-upload "files" for a project.

Each project gets a folder under `bran_home/projects/<id>/files/`. The user
uploads documents (PDFs, CSVs, notes, code) into it from the SPA; bran then
tells every agent running in that project where the folder is and what's in it,
so the agent can `Read`/`Glob`/`Grep` (and `read_pdf`) the user's material on
demand. This is the "work *with* my files" half of the Cowork model.

The folder lives under `bran_home`, which is exactly the region the
write-confinement hook (see `bran.permissions`) already allows agents to write
into — so an agent can also save outputs back here without any new sandbox
holes. cwd stays at the project root (the SDK needs it for `.claude` discovery),
so the agent works by the folder's *absolute* path, which we hand it in the
system prompt.

Filenames are sanitised to a single path component (no traversal): everything
operates strictly inside the project's files dir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bran.config import SETTINGS

# Don't list more than this many files in the system prompt (keeps the prompt
# bounded if someone dumps a big folder in); the agent can still Glob the dir.
_MAX_PROMPT_FILES = 50


def _projects_root() -> Path:
    return SETTINGS.bran_home / "projects"


def files_dir(project_id: str, *, create: bool = False) -> Path:
    """The absolute path to a project's working-files folder.

    `create=True` makes the folder (and parents) if missing — used on upload and
    when assembling the prompt so the agent is always pointed at a real dir.
    """
    d = _projects_root() / project_id / "files"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(name: str) -> str:
    """Reduce an uploaded filename to a single, safe path component.

    Strips any directory parts (`a/b/../c.pdf` -> `c.pdf`) and rejects names
    that don't reduce to a real file component, closing path-traversal. Raises
    ValueError on anything unusable.
    """
    base = Path(name.replace("\\", "/")).name.strip()
    if not base or base in (".", "..") or "/" in base:
        raise ValueError(f"unsafe filename: {name!r}")
    return base


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/(1024*1024):.1f} MB"
    return f"{n/(1024*1024*1024):.1f} GB"


def list_files(project_id: str) -> list[dict[str, Any]]:
    """Files in the project's folder, newest first. Empty list if none/no dir."""
    d = files_dir(project_id)
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        st = p.stat()
        out.append(
            {
                "name": p.name,
                "size": st.st_size,
                "size_human": _fmt_size(st.st_size),
                "modified": st.st_mtime,
                "path": str(p),
            }
        )
    out.sort(key=lambda f: f["modified"], reverse=True)
    return out


def save_upload(project_id: str, filename: str, data: bytes) -> dict[str, Any]:
    """Write an uploaded file into the project's folder, returning its metadata.

    Overwrites a same-named file (last upload wins) — the UI shows the list so
    this is predictable.
    """
    safe = _safe_name(filename)
    d = files_dir(project_id, create=True)
    dest = d / safe
    dest.write_bytes(data)
    st = dest.stat()
    return {
        "name": safe,
        "size": st.st_size,
        "size_human": _fmt_size(st.st_size),
        "modified": st.st_mtime,
        "path": str(dest),
    }


def delete_file(project_id: str, filename: str) -> bool:
    """Remove a single file from the project's folder. False if it didn't exist."""
    safe = _safe_name(filename)
    target = files_dir(project_id) / safe
    if not target.is_file():
        return False
    target.unlink()
    return True


def files_prompt(project_id: str) -> str | None:
    """The "## Files" system-prompt section for a project, or None if empty.

    Hands the agent the folder's absolute path and a manifest of what's in it,
    plus how to use them. None when the project has no files (so we don't bloat
    prompts with an empty section).
    """
    files = list_files(project_id)
    if not files:
        return None
    d = files_dir(project_id, create=True)
    lines = [
        "## Files",
        (
            "This project has working files the user uploaded. They live in this "
            f"folder (use absolute paths): `{d}`"
        ),
        "",
    ]
    for f in files[:_MAX_PROMPT_FILES]:
        lines.append(f"- `{f['name']}` ({f['size_human']})")
    if len(files) > _MAX_PROMPT_FILES:
        lines.append(f"- …and {len(files) - _MAX_PROMPT_FILES} more (Glob the folder to see all).")
    lines += [
        "",
        (
            "Use `Read`/`Glob`/`Grep` on that folder to consult them, and "
            "`mcp__bran_docs__read_pdf` for any `.pdf` (the plain Read tool can't "
            "parse PDFs). Prefer these files over guessing when the user refers "
            "to “my doc”, “the file”, “the report”, etc."
        ),
    ]
    return "\n".join(lines)
