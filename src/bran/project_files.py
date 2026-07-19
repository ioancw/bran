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


def save_attachment(filename: str, data: bytes) -> dict[str, Any]:
    """Save a chat-composer attachment under `bran_home/uploads/`.

    Unlike project files (a stable per-project library), attachments are
    one-off inputs to a conversation: each gets a short unique prefix so
    same-named uploads never collide, and the chat references them by absolute
    path in the prompt. They live under bran_home, which every agent can Read.
    """
    import uuid

    safe = _safe_name(filename)
    d = SETTINGS.bran_home / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{uuid.uuid4().hex[:8]}_{safe}"
    dest.write_bytes(data)
    return {"name": safe, "path": str(dest), "size_human": _fmt_size(len(data))}


def workdir_prompt(work_dir: str) -> str | None:
    """The "## Working folder" system-prompt section for a project, or None.

    Unlike the managed uploads folder, the working folder is a real directory
    the *user* chose — agents may read AND write there (the confinement hook
    admits it), so deliverables (reports, CSVs, edited copies) land where the
    user actually works. Validation happens at save time and again in the
    hook; here we just describe whatever is configured.
    """
    wd = (work_dir or "").strip()
    if not wd:
        return None
    return "\n".join([
        "## Working folder",
        (
            f"This project has a working folder at `{wd}` (use absolute paths). "
            "You may read AND write files there: when a task produces a file — "
            "a report, a spreadsheet, a cleaned-up copy — create it in this "
            "folder rather than answering only in prose. Use `Read`/`Glob`/"
            "`Grep` to consult what's already there, and `Write`/`Edit` to "
            "produce or update files. Tell the user the full path of anything "
            "you create."
        ),
    ])


def files_prompt(project_id: str) -> str | None:
    """The "## Files" system-prompt section for a project.

    Describes the project's files folder as the shared read+write workspace: the
    user uploads material into it, and agents both consult it AND save their own
    deliverables back into it (so uploads and outputs live in one place). Always
    present for a project — the folder is a capability even when empty.
    """
    d = files_dir(project_id, create=True)
    files = list_files(project_id)
    lines = [
        "## Files",
        (
            "This project has a files folder — the shared workspace for this "
            f"project (use absolute paths): `{d}`. The user uploads material "
            "here, and you save what you produce here too, so inputs and outputs "
            "live together."
        ),
        "",
    ]
    if files:
        lines.append("Currently in the folder:")
        for f in files[:_MAX_PROMPT_FILES]:
            lines.append(f"- `{f['name']}` ({f['size_human']})")
        if len(files) > _MAX_PROMPT_FILES:
            lines.append(f"- …and {len(files) - _MAX_PROMPT_FILES} more (Glob the folder to see all).")
    else:
        lines.append("The folder is currently empty.")
    lines += [
        "",
        (
            "Reading: use `Read`/`Glob`/`Grep` on that folder, and "
            "`mcp__bran_docs__read_pdf` for any `.pdf` (the plain Read tool "
            "can't parse PDFs). Prefer these files over guessing when the user "
            "refers to “my doc”, “the file”, “the report”, etc."
        ),
        (
            "Producing files: when the user wants a report/brief/write-up as a "
            "file or PDF, call `mcp__bran_docs__save_document` (title + Markdown "
            "body, with `\\(inline\\)` / `$$display$$` math) — it writes a "
            "self-contained HTML file here that opens in Chrome and prints to "
            "PDF. If you hold the `Write` tool you may also write raw files "
            "(e.g. a `.tex` source, a `.csv`) into this folder. Always tell the "
            "user the file name and that it's in the project's Files."
        ),
    ]
    return "\n".join(lines)
