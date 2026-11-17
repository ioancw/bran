"""Teammates: an agent with a name, a standing brief, one chat and its own
automations (see `bran.teammates`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, status

from bran.persistence import delete_teammate, get_teammate, list_teammates
from bran.teammates import (
    TeammateError,
    assign_automation,
    create_teammate,
    deliveries,
    edit_teammate,
    teammate_dict,
)
from bran.web.spa_routes.common import form_flag

router = APIRouter()

_STATUS = {
    "invalid": status.HTTP_400_BAD_REQUEST,
    "conflict": status.HTTP_409_CONFLICT,
    "missing": status.HTTP_404_NOT_FOUND,
}


def _http(e: TeammateError) -> HTTPException:
    return HTTPException(_STATUS.get(e.kind, status.HTTP_400_BAD_REQUEST), str(e))


def _or_404(teammate_id: str):
    t = get_teammate(teammate_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such teammate.")
    return t


@router.get("/teammates")
def teammates() -> list[dict[str, Any]]:
    return [teammate_dict(t) for t in list_teammates()]


@router.get("/teammates/{teammate_id}")
def teammate(teammate_id: str) -> dict[str, Any]:
    """One teammate, plus what its automations delivered most recently."""
    t = _or_404(teammate_id)
    done = [
        {
            "id": r.id, "schedule_id": r.schedule_id, "started_at": r.started_at,
            "ended_at": r.ended_at, "excerpt": " ".join((r.result or "").split())[:220],
        }
        for r in deliveries(t)
    ]
    return {**teammate_dict(t), "deliveries": done}


@router.post("/teammates")
async def new_teammate(
    name: Annotated[str, Form()],
    agent: Annotated[str, Form()],
    brief: Annotated[str, Form()] = "",
    schedule: Annotated[str, Form()] = "",
    adopt: Annotated[str, Form()] = "",
    project_id: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """Create a teammate. `schedule` (plain words or cron) also creates the
    automation that does its brief; `adopt` hands it an existing one."""
    try:
        rec = create_teammate(
            name=name, agent=agent, brief=brief, schedule=schedule, adopt=adopt,
            project_id=project_id.strip() or None,
        )
    except TeammateError as e:
        raise _http(e)
    return teammate_dict(rec)


@router.post("/teammates/{teammate_id}")
def update(
    teammate_id: str,
    name: Annotated[str, Form()],
    agent: Annotated[str, Form()],
    brief: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    try:
        return teammate_dict(edit_teammate(teammate_id, name=name, agent=agent, brief=brief))
    except TeammateError as e:
        raise _http(e)


@router.post("/teammates/{teammate_id}/automations")
def set_automation(
    teammate_id: str,
    name: Annotated[str, Form()],
    attach: Annotated[str, Form()] = "true",
) -> dict[str, Any]:
    """Hand an existing automation to the teammate, or take it back."""
    try:
        assign_automation(teammate_id, name, form_flag(attach))
    except TeammateError as e:
        raise _http(e)
    return teammate_dict(_or_404(teammate_id))


@router.delete("/teammates/{teammate_id}")
def remove(teammate_id: str) -> dict[str, Any]:
    """Remove the teammate. Its automations stay as plain automations and its
    chat as an ordinary chat."""
    if not delete_teammate(teammate_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such teammate.")
    return {"deleted": teammate_id}
