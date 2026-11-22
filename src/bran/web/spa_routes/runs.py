"""Runs: the activity log, a run's transcript/sources/artifacts, the live
stream, and the Outputs reading state."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, status
from fastapi.responses import FileResponse, Response, StreamingResponse

from bran.agents import get_agent
from bran.persistence import (
    RunRecord,
    get_output_states,
    get_run,
    list_runs,
    mark_outputs_read,
    set_output_starred,
)
from bran.sources import sources_for_run
from bran.transcript import find_session_file, parse_transcript
from bran.web.events import events_from_runs, events_from_transcript_entry
from bran.web.spa_routes.common import (
    LIVE,
    attach_child_run,
    form_flag,
    launch_run,
    run_dict,
    run_or_404,
)

router = APIRouter()


@router.get("/runs")
def runs(
    agent: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    project_id: str | None = None, schedule_id: str | None = None,
    exclude_chats: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    q: str | None = None,
    session_id: str | None = None, parent_run_id: str | None = None,
    exclude_subagents: bool = False,
) -> list[dict[str, Any]]:
    from bran.persistence import list_artifacts_for_runs

    records = list_runs(agent=agent or None, status=status_ or None,
                        exclude_subagents=exclude_subagents,
                        project_id=project_id or None,
                        schedule_id=schedule_id or None,
                        exclude_chats=exclude_chats, limit=limit, q=q or None,
                        session_id=session_id or None,
                        parent_run_id=parent_run_id or None)
    ids = [r.id for r in records]
    artifacts = list_artifacts_for_runs(ids)
    states = get_output_states(ids)
    out = []
    for r in records:
        d = run_dict(r)
        d["artifacts"] = artifacts.get(r.id, [])
        st = states.get(r.id)
        d["starred"] = bool(st["starred"]) if st else False
        d["read_at"] = st["read_at"] if st else None
        out.append(d)
    return out


@router.get("/runs/counts")
def run_counts() -> dict[str, Any]:
    """True totals for the activity log (the list itself is capped): the runs
    someone or something started, and the sub-agent steps inside them."""
    from bran.persistence import count_runs_by_source

    by_source = count_runs_by_source()
    steps = by_source.get("subagent", 0)
    return {"runs": sum(by_source.values()) - steps, "subagent": steps, "by_source": by_source}


@router.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    return run_dict(run_or_404(run_id))


# --- Output reading state (star / read) — durable, cross-browser ----------

@router.post("/outputs/{run_id}/star")
def star_output(
    run_id: str, starred: Annotated[str, Form()] = "true",
) -> dict[str, Any]:
    run_or_404(run_id)
    want = form_flag(starred)
    set_output_starred(run_id, want)
    return {"run_id": run_id, "starred": want}


@router.post("/outputs/read")
def mark_read(ids: Annotated[str, Form()] = "") -> dict[str, Any]:
    """Mark a comma-separated batch of runs read (opening the Outputs inbox)."""
    run_ids = [x for x in (s.strip() for s in ids.split(",")) if x]
    mark_outputs_read(run_ids)
    return {"read": len(run_ids)}


@router.post("/runs")
async def new_run(
    agent: Annotated[str, Form()],
    task: Annotated[str, Form()],
    project_id: Annotated[str | None, Form()] = None,
    schedule_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    try:
        get_agent(agent)
    except KeyError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    record = launch_run(
        agent, task, project_id=project_id or None, schedule_id=schedule_id or None,
    )
    return asdict(record)


@router.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict[str, Any]:
    """Remove a finished run from the activity log and Outputs. A live run
    must be cancelled first — deleting under a running process would leave
    it writing into a row that no longer exists."""
    from bran.persistence import delete_runs

    rec = run_or_404(run_id)
    if rec.status in LIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancel the run before deleting it.")
    return {"deleted": delete_runs([run_id])}


@router.post("/runs/delete")
def delete_runs_batch(ids: Annotated[str, Form()] = "") -> dict[str, Any]:
    """Delete several finished runs at once (Today's 'Clear' on failures).
    Live runs in the list are skipped, not refused."""
    from bran.persistence import delete_runs

    wanted = [x for x in (s.strip() for s in ids.split(",")) if x]
    finished = [r for r in wanted if (rec := get_run(r)) is not None and rec.status not in LIVE]
    return {"deleted": delete_runs(finished), "skipped": len(wanted) - len(finished)}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    from bran.background import cancel_background

    run_or_404(run_id)
    return {"run_id": run_id, "cancelled": cancel_background(run_id)}


@router.get("/runs/{run_id}/transcript")
def run_transcript(run_id: str) -> dict[str, Any]:
    rec = run_or_404(run_id)
    events: list[dict[str, Any]] = []
    path = find_session_file(rec.session_id) if rec.session_id else None
    if path is not None:
        for entry in parse_transcript(path):
            events.extend(events_from_transcript_entry(entry))
    else:
        # SDK transcript pruned (or never on this host): replay the copy the
        # run kept, or at least its prompt + final result.
        events = events_from_runs([rec])
    return {"run": run_dict(rec), "events": [attach_child_run(e) for e in events]}


@router.get("/runs/{run_id}/export.html")
def run_export_html(run_id: str) -> Response:
    """The run's report as ONE self-contained HTML file: it opens anywhere,
    offline, and prints cleanly to PDF (the engine behind save_document)."""
    import re
    from datetime import datetime

    from bran.doc_export import DocAssetsMissing, render_document_html, slugify

    rec = run_or_404(run_id)
    text = (rec.result or "").strip()
    if not text:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This run has no report to download.")
    # The report's own first heading is the document's title.
    m = re.match(r"\s*#\s+(.+?)\s*(?:\n|$)", text)
    title = m.group(1).strip() if m else f"{rec.agent} report"
    body = text[m.end():] if m else text
    try:
        when = datetime.fromisoformat(rec.ended_at or rec.started_at).astimezone()
        dated = f"{when.strftime('%A')} {when.day} {when.strftime('%B %Y, %H:%M')}"
    except (TypeError, ValueError):
        dated = ""
    try:
        html = render_document_html(title, body, subtitle=" · ".join(x for x in (rec.agent, dated) if x))
    except DocAssetsMissing as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    # Named for who wrote it and when: short, and it sorts by date.
    stamp = (rec.ended_at or rec.started_at or "")[:10]
    name = "-".join(x for x in (slugify(rec.agent, "report"), stamp) if x)
    return Response(
        html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}.html"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runs/{run_id}/sources")
def run_sources(run_id: str) -> list[dict[str, Any]]:
    return sources_for_run(run_or_404(run_id))


@router.get("/runs/{run_id}/stream")
async def run_stream(run_id: str) -> StreamingResponse:
    """Watch a background run execute: SSE of its unified events, live.

    Subscribers attaching mid-run get a full replay first (see bran.live). If
    the run already finished — or isn't live in this process (server restart)
    — the stream ends immediately with `done` and the client falls back to the
    stored transcript.
    """
    rec = run_or_404(run_id)

    def sse(ev: dict[str, Any]) -> str:
        return f"data: {json.dumps(ev, default=str)}\n\n"

    async def gen():
        from bran.live import is_live, subscribe

        if rec.status in LIVE and is_live(run_id):
            async for ev in subscribe(run_id):
                yield sse(attach_child_run(ev))
        yield sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Artifacts — files a run produced (captured from its Write/Edit tool calls)
# ---------------------------------------------------------------------------

def _artifact_entries(rec: RunRecord) -> list[dict[str, Any]]:
    """Existence-checked listing of a run's recorded artifacts."""
    from bran.persistence import list_artifacts

    out: list[dict[str, Any]] = []
    for i, raw in enumerate(list_artifacts(rec.id)):
        p = Path(raw)
        exists = p.is_file()
        out.append({
            "index": i,
            "name": p.name,
            "path": str(p),
            "exists": exists,
            "size": p.stat().st_size if exists else None,
        })
    return out


@router.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: str) -> list[dict[str, Any]]:
    return _artifact_entries(run_or_404(run_id))


@router.get("/runs/{run_id}/artifacts/{index}")
def download_artifact(run_id: str, index: int) -> FileResponse:
    """Download one recorded artifact by its position in the run's list.

    Index-based (not path-based) so no client-supplied path ever reaches the
    filesystem. Defense in depth: even the *recorded* path must still resolve
    inside the run's sanctioned write roots at serve time — guards against a
    stale work_dir being repointed somewhere sensitive after the run.
    """
    rec = run_or_404(run_id)
    from bran.persistence import list_artifacts

    entries = list_artifacts(run_id)
    if not (0 <= index < len(entries)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No artifact #{index} on run {run_id}")
    from bran.permissions import write_roots_for_project

    p = Path(entries[index]).resolve()
    roots = write_roots_for_project(rec.project_id)
    if not any(p == r or r in p.parents for r in roots):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "artifact path no longer sanctioned")
    if not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"artifact no longer exists: {p.name}")
    # Always a download (attachment), and never sniffed into something else.
    return FileResponse(p, filename=p.name, headers={"X-Content-Type-Options": "nosniff"})
