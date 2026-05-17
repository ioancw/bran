"""FastAPI server exposing the agent fleet over HTTP + a web UI.

Layout:
    /                 web UI (no auth — gated by the get_current_user stub)
    /runs, /agents, /schedules, /ui/*    UI pages and HTMX partials
    /api/agents             — list agents                    (bearer auth)
    /api/agents/{name}/run  — fire an agent run              (bearer auth)
    /api/runs               — list recent runs               (bearer auth)
    /api/runs/{id}          — inspect a single run           (bearer auth)
    /api/schedules          — list/create schedules          (bearer auth)
    /api/schedules/{name}   — delete a schedule              (bearer auth)
    /healthz                — health probe                   (no auth)

The JSON API moved under /api/* in v0.2 to free up the root paths for the UI.
The scheduler runs in-process and starts/stops with the app's lifespan unless
disabled at startup.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Header,
    Path,
    Query,
    status,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bran.config import SETTINGS
from bran.persistence import (
    ScheduleRecord,
    delete_schedule,
    get_run,
    insert_schedule,
    list_runs,
    list_schedules,
)
from bran.agents import list_agents
from bran.runner import run_agent
from bran.web.routes import router as ui_router


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def _require_token(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    expected = SETTINGS.api_token
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Server has no BRAN_API_TOKEN configured.",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    task: str = Field(..., description="The prompt/task to send the agent.")
    resume: str | None = Field(None, description="Session ID to resume.")
    max_turns: int | None = None
    background: bool = Field(
        False,
        description="If true, return immediately with a run_id and execute asynchronously.",
    )


class ScheduleCreateRequest(BaseModel):
    name: str
    agent: str
    task: str
    cron: str


# ---------------------------------------------------------------------------
# JSON API router (mounted at /api)
# ---------------------------------------------------------------------------

def _build_api_router(enable_scheduler: bool) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["api"], dependencies=[Depends(_require_token)])

    @api.get("/agents")
    async def agents() -> list[dict[str, Any]]:
        return [
            {
                "name": p.name,
                "description": p.description,
                "model": p.model,
                "tools": p.tools,
                "subagents": list(p.subagents),
            }
            for p in list_agents()
        ]

    @api.post("/agents/{name}/run")
    async def run_agent_endpoint(
        name: Annotated[str, Path()],
        body: RunRequest,
    ) -> dict[str, Any]:
        if body.background:
            from bran.persistence import RunRecord, insert_run

            record = RunRecord.new(agent=name, task=body.task)
            insert_run(record)

            async def _go():
                try:
                    await run_agent(
                        name,
                        body.task,
                        resume_session=body.resume,
                        max_turns=body.max_turns,
                        record=record,
                    )
                except Exception:
                    pass

            asyncio.create_task(_go(), name=f"http-spawn:{record.id}")
            return {"run_id": record.id, "status": "running", "background": True}

        try:
            record = await run_agent(
                name,
                body.task,
                resume_session=body.resume,
                max_turns=body.max_turns,
            )
        except KeyError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
        return asdict(record)

    @api.get("/runs")
    async def runs(
        agent: str | None = Query(None),
        status_: str | None = Query(None, alias="status"),
        limit: int = Query(50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return [asdict(r) for r in list_runs(agent=agent, status=status_, limit=limit)]

    @api.get("/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, Any]:
        rec = get_run(run_id)
        if rec is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
        return asdict(rec)

    @api.get("/schedules")
    async def schedules() -> list[dict[str, Any]]:
        return [asdict(s) for s in list_schedules()]

    @api.post("/schedules", status_code=status.HTTP_201_CREATED)
    async def create_schedule(body: ScheduleCreateRequest) -> dict[str, Any]:
        rec = ScheduleRecord.new(
            name=body.name, agent=body.agent, task=body.task, cron=body.cron
        )
        insert_schedule(rec)
        if enable_scheduler:
            from bran.scheduler import register_schedule

            register_schedule(rec)
        return asdict(rec)

    @api.delete("/schedules/{name}")
    async def remove_schedule(name: str) -> dict[str, Any]:
        if not delete_schedule(name):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No schedule {name}")
        if enable_scheduler:
            from bran.scheduler import unregister_schedule

            unregister_schedule(name)
        return {"deleted": name}

    return api


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def build_app(enable_scheduler: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if enable_scheduler:
            from bran.scheduler import start_scheduler, stop_scheduler

            start_scheduler()
            try:
                yield
            finally:
                stop_scheduler()
        else:
            yield

    app = FastAPI(
        title="bran",
        version="0.1.0",
        description="Fleet-orchestration API + web UI for Claude Agent SDK agents.",
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Static assets (CSS, fonts, etc.) served at /static/*.
    from pathlib import Path as _Path
    static_dir = _Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(_build_api_router(enable_scheduler))
    app.include_router(ui_router)
    return app


# Module-level app instance for `uvicorn bran.api:app`.
app = build_app(enable_scheduler=False)
