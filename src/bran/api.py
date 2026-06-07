"""FastAPI server: the Svelte SPA + JSON APIs.

Layout:
    /                       the Svelte SPA (served from web/spa/), no auth
    /spa/*                  JSON API the SPA consumes                (no auth)
    /api/*                  external JSON API                       (bearer auth)
    /static/*               static assets (favicon)
    /healthz                health probe                            (no auth)

The bearer-auth /api/* surface is for external/programmatic callers; the SPA
uses the unauthenticated /spa/* surface (localhost-first). The scheduler runs
in-process and starts/stops with the app's lifespan unless disabled at startup.
"""

from __future__ import annotations

import logging
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

from bran import __version__
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
from bran.background import spawn_background
from bran.runner import run_agent


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

            spawn_background(_go(), name=f"http-spawn:{record.id}")
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

    @api.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        """Cancel an in-flight background run. Returns how many tasks were
        cancelled (0 if the run isn't a live background task on this process)."""
        from bran.background import cancel_background

        if get_run(run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No run {run_id}")
        return {"run_id": run_id, "cancelled": cancel_background(run_id)}

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
        # Crash recovery: any run left 'pending'/'running' is an orphan from a
        # previous process — mark it failed so it doesn't show in-progress forever.
        try:
            from bran.persistence import reconcile_interrupted_runs

            n = reconcile_interrupted_runs()
            if n:
                logging.getLogger("bran.api").info("reconciled %d interrupted run(s) on startup", n)
        except Exception:
            logging.getLogger("bran.api").exception("run reconciliation failed")

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
        version=__version__,
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
    from bran.web.spa_api import router as spa_router
    app.include_router(spa_router)
    # The Svelte SPA is now the primary UI, served at / (cutover from the legacy
    # HTMX/Jinja UI, which has been removed). Mounted last so its catch-all
    # doesn't shadow /api, /spa, /static, /healthz (all registered above).
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the built Svelte SPA at the site root (no-op until it's built).

    Vite builds to web/spa/ with base '/'. Hashed JS/CSS live under
    web/spa/assets/ (served by a StaticFiles mount); every other non-API path
    returns index.html so client-side (history) routing survives a refresh.
    """
    from pathlib import Path as _Path

    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    spa_dir = _Path(__file__).parent / "web" / "spa"
    index = spa_dir / "index.html"

    assets = spa_dir / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="spa-assets")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
    async def spa_index(path: str = ""):
        if not index.exists():
            return JSONResponse(
                {"detail": "Frontend not built. Run: cd frontend && npm run build"},
                status_code=503,
            )
        return FileResponse(index)


def _spa_built() -> bool:
    from pathlib import Path as _Path

    return (_Path(__file__).parent / "web" / "spa" / "index.html").exists()


# Module-level app instance for `uvicorn bran.api:app`.
app = build_app(enable_scheduler=False)
