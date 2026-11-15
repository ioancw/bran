"""Guards for the token-free SPA surface."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request, status


async def require_same_origin(request: Request) -> None:
    """CSRF guard for the unauthenticated SPA surface.

    /spa is token-free (localhost-first), but several POST handlers take form
    bodies — CORS "simple requests" that any web page the user visits can fire
    at 127.0.0.1 without a preflight, silently starting agent runs or creating
    schedules. Browsers tell us where a request came from, so reject anything
    cross-site: via Sec-Fetch-Site when present (all modern browsers), else by
    comparing the Origin header's host against the request Host. Requests with
    neither header come from non-browser clients (curl, httpx) where CSRF
    doesn't apply.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        # "none" = direct navigation (address bar / bookmark) — user-initiated.
        if site not in ("same-origin", "none"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Cross-origin requests are not allowed"
            )
        return
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit

        # "null" (sandboxed iframe, data: URI) is also cross-origin.
        if origin == "null" or urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Cross-origin requests are not allowed"
            )


def untrusted_file_headers(path: Path) -> dict[str, str]:
    """Headers for a file an agent (or an upload) put on disk, served from the
    app's own origin. Such a file is untrusted: a prompt-injected agent can
    write an .html or .svg whose script would otherwise run same-origin and
    drive /spa as the user. `sandbox` gives the document an opaque origin, so
    its requests fail the same-origin guard; scripts and print dialogs still
    work (KaTeX documents, print-to-PDF). PDFs are exempt because the
    browser's PDF viewer refuses to load in a sandboxed document; `nosniff`
    keeps one from being reinterpreted as HTML."""
    import mimetypes

    headers = {"X-Content-Type-Options": "nosniff"}
    if mimetypes.guess_type(path.name)[0] != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox allow-scripts allow-modals"
    return headers
