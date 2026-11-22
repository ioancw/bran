"""What the UI needs around an answer: site icons for its sources, suggested
follow-up questions, the user's thumbs up/down, and documents saved outside a
project (so the chat can show them beside the conversation)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Response, status
from fastapi.responses import FileResponse

from bran.web.spa_routes.security import untrusted_file_headers

router = APIRouter()

_FEEDBACK_TEXT_CAP = 20_000


@router.get("/favicon/{domain}")
async def favicon(domain: str) -> Response:
    """The icon of a cited site (see bran.favicons). 404 when it has none."""
    from bran.favicons import favicon_for, valid_domain

    domain = domain.strip().lower()
    if not valid_domain(domain):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a domain name.")
    icon = await favicon_for(domain)
    if icon is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": "max-age=3600"})
    data, media_type = icon
    return Response(
        data, media_type=media_type,
        headers={"Cache-Control": "max-age=604800", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/followups")
async def followups(
    prompt: Annotated[str, Form()] = "", answer: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """What to offer under the answer just given: replies to the question it
    ends on (kind "replies"), else up to three next questions ("questions")."""
    from bran.followups import suggest

    return await suggest(prompt, answer)


@router.get("/chats/{chat_id}/feedback")
def chat_feedback(chat_id: str) -> dict[str, int]:
    """The ratings given in this chat: answer key -> 1 / -1."""
    from bran.persistence import list_answer_feedback

    return list_answer_feedback(chat_id)


@router.post("/chats/{chat_id}/feedback")
def rate_answer(
    chat_id: str,
    key: Annotated[str, Form()],
    rating: Annotated[int, Form()],
    prompt: Annotated[str, Form()] = "",
    answer: Annotated[str, Form()] = "",
    agent: Annotated[str, Form()] = "",
) -> dict[str, Any]:
    """Thumbs up (1), down (-1) or cleared (0) for one answer. The question
    and answer are kept with the rating: together they are the start of an
    evaluation set for the agents."""
    from bran.persistence import set_answer_feedback

    key = key.strip()
    if not key or len(key) > 64:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An answer key is required.")
    if rating not in (-1, 0, 1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rating must be -1, 0 or 1.")
    set_answer_feedback(
        chat_id, key, rating,
        prompt=prompt[:_FEEDBACK_TEXT_CAP], answer=answer[:_FEEDBACK_TEXT_CAP], agent=agent[:80],
    )
    return {"chat_id": chat_id, "key": key, "rating": rating}


@router.get("/documents/{filename}")
def get_document(filename: str, download: bool = False) -> FileResponse:
    """Serve a document an agent saved outside any project (bran_home/documents).
    Untrusted content, like a project file — see `untrusted_file_headers`."""
    from bran.doc_export import documents_dir
    from bran.project_files import _safe_name

    try:
        safe = _safe_name(filename)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    root = documents_dir().resolve()
    p = (root / safe).resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No document {filename!r}")
    return FileResponse(
        p, filename=safe,
        content_disposition_type="attachment" if download else "inline",
        headers=untrusted_file_headers(p),
    )
