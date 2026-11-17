"""JSON API for the Svelte SPA (token-free, localhost-first; same-origin only).

This is the single contract the frontend consumes. Chat live-stream and replay
both emit the unified schema from `bran.web.events`, so the client renders them
through one path. There is no bearer token on this surface, but every route is
gated by `require_same_origin` so other websites in the user's browser can't
fire requests at it (CSRF).

The handlers live in `bran.web.spa_routes`, one module per area; this module
only assembles them under `/spa` behind the guard.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from bran.web.spa_routes import answers, chats, meta, projects, runs, schedules, teammates
from bran.web.spa_routes.security import require_same_origin

router = APIRouter(
    prefix="/spa", tags=["spa"], dependencies=[Depends(require_same_origin)]
)
for _module in (meta, runs, schedules, projects, answers, chats, teammates):
    router.include_router(_module.router)
