"""Document-ingestion MCP tools: read_pdf.

Fills a real gap — the SDK's `Read` tool handles text files but not binary PDFs
(filings, papers, reports), which research/finance work leans on. `read_pdf`
extracts text from a local file OR an http(s) URL and hands it back for the
agent to summarise/analyse. Adapted from circus's `tools/pdf.py`.

Local reads are intentionally NOT path-sandboxed: bran is a single-user,
localhost tool that already exposes the open SDK `Read` tool, and it runs from
WSL while the user's files live on the Windows drive (`/mnt/c/...`), so a
home/project sandbox would just reject the files you actually want. Reads are
size-capped; URLs are size- and timeout-capped.

Exposed via the `bran` MCP server; the tool becomes `mcp__bran__read_pdf`.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from claude_agent_sdk import create_sdk_mcp_server, tool

_MAX_PAGES = 50
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_TIMEOUT_S = 30.0
_MAX_TEXT_CHARS = 600_000  # cap returned text so a huge page can't blow the buffer
_MAX_REDIRECTS = 5


async def _reject_non_public(url: str) -> str | None:
    """SSRF/egress guard: return an error string if the URL's host resolves to
    any non-public address (loopback, RFC1918, link-local incl. 169.254.169.254
    cloud metadata, CGN, reserved), else None.

    These tools ingest UNTRUSTED web content into agents that also hold the
    Read tool — without this check, a prompt-injected agent could be steered
    into fetching internal services or exfiltrating to a rebound hostname that
    resolves locally. Set BRAN_FETCH_ALLOW_PRIVATE=1 to disable (e.g. to pull
    a feed from a genuinely internal server you trust).
    """
    if os.getenv("BRAN_FETCH_ALLOW_PRIVATE") == "1":
        return None
    host = urlsplit(url).hostname
    if not host:
        return f"couldn't parse a hostname from {url!r}"
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError as e:
        return f"couldn't resolve {host}: {e}"
    for info in infos:
        addr = str(info[4][0]).partition("%")[0]  # strip IPv6 zone id
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not ip.is_global:
            return (
                f"{host} resolves to {ip}, a non-public address — fetching "
                "loopback/private/link-local hosts is blocked "
                "(BRAN_FETCH_ALLOW_PRIVATE=1 overrides)"
            )
    return None


async def _fetch_checked(url: str, headers: dict[str, str]):
    """GET a URL with the egress guard applied to every redirect hop, streaming
    the body so oversized responses are aborted at the cap instead of buffered
    whole into memory. Returns (body_bytes, final_response).

    Raises ValueError with a user-facing message on any policy or HTTP failure.
    """
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=False, timeout=_TIMEOUT_S, headers=headers
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"redirected to a non-http(s) URL: {url!r}")
            err = await _reject_non_public(url)
            if err:
                raise ValueError(err)
            async with client.stream("GET", url) as resp:
                if resp.is_redirect:
                    nxt = resp.next_request
                    url = str(nxt.url) if nxt is not None else urljoin(
                        url, resp.headers.get("location", "")
                    )
                    continue
                if resp.status_code >= 400:
                    raise ValueError(f"HTTP {resp.status_code} fetching {url}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        raise ValueError(
                            f"response exceeds the {_MAX_BYTES:,} byte limit — aborted"
                        )
                    chunks.append(chunk)
                return b"".join(chunks), resp
        raise ValueError(f"too many redirects (>{_MAX_REDIRECTS}) fetching {url}")


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Error: " + text}]}


def _read_local(path_str: str) -> bytes:
    candidate = Path(os.path.expanduser(path_str)).resolve()
    if not candidate.is_file():
        raise ValueError(f"no file at {candidate}")
    size = candidate.stat().st_size
    if size > _MAX_BYTES:
        raise ValueError(f"file is {size:,} bytes — exceeds the {_MAX_BYTES:,} byte limit")
    return candidate.read_bytes()


async def _read_remote(url: str) -> bytes:
    data, _ = await _fetch_checked(url, {"User-Agent": "bran/0.1 (+read_pdf)"})
    return data


def _extract_text(data: bytes) -> tuple[str, int, int]:
    """Return (text, pages_read, total_pages)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    n = min(total, _MAX_PAGES)
    parts: list[str] = []
    for i in range(n):
        try:
            parts.append(reader.pages[i].extract_text() or "")
        except Exception as e:  # corrupt/odd page — keep going
            parts.append(f"[error extracting page {i + 1}: {e}]")
    return "\n\n".join(parts).strip(), n, total


@tool(
    "read_pdf",
    (
        "Extract the text of a PDF so you can read/summarise/analyse it. `source` "
        "is either a local file path (e.g. '/mnt/c/Users/.../report.pdf') or an "
        "http(s) URL to a PDF. Returns the text (up to 50 pages). Use this for "
        "filings, papers, reports — the plain Read tool can't parse PDFs. If it "
        "comes back empty the PDF is likely scanned images (no OCR)."
    ),
    {"source": str},
)
async def read_pdf(args: dict[str, Any]) -> dict[str, Any]:
    source = (args.get("source") or "").strip()
    if not source:
        return _err("a source is required (a local file path or an http(s) URL).")
    try:
        if source.startswith(("http://", "https://")):
            data = await _read_remote(source)
        else:
            data = _read_local(source)
        text, pages_read, total = _extract_text(data)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"couldn't read the PDF: {e}")

    if not text:
        return _ok(
            f"Read {source} ({total} page{'s' if total != 1 else ''}) but extracted no text — "
            "it's probably a scanned/image PDF (bran has no OCR)."
        )
    head = f"PDF: {source} — read {pages_read} of {total} pages"
    if pages_read < total:
        head += f" (truncated at {_MAX_PAGES})"
    return _ok(f"{head}\n\n{text}")


@tool(
    "fetch_url",
    (
        "Fetch the raw text of a web URL — an RSS/Atom feed, an HTML page, a JSON "
        "or plain-text document — and return it. Uses a plain HTTP client with NO "
        "publisher blocklist, so it reaches feeds/sites the built-in `WebFetch` "
        "tool refuses or can't load (e.g. ft.com). `url` must be http(s). Returns "
        "the response body as text (up to ~600k chars). For PDFs use `read_pdf` "
        "instead. This returns the raw body (e.g. RSS XML) — parse it yourself."
    ),
    {"url": str},
)
async def fetch_url(args: dict[str, Any]) -> dict[str, Any]:
    url = (args.get("url") or "").strip()
    if not url:
        return _err("a url is required (an http(s) URL).")
    if not url.startswith(("http://", "https://")):
        return _err("url must start with http:// or https://")

    headers = {"User-Agent": "bran/0.1 (+fetch_url)", "Accept": "*/*"}
    try:
        data, resp = await _fetch_checked(url, headers)
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"couldn't fetch {url}: {e}")

    text = data.decode(resp.encoding or "utf-8", errors="replace")
    ctype = resp.headers.get("content-type", "")
    truncated = len(text) > _MAX_TEXT_CHARS
    if truncated:
        text = text[:_MAX_TEXT_CHARS]
    head = f"GET {url} → HTTP {resp.status_code}"
    if ctype:
        head += f" ({ctype})"
    if truncated:
        head += f" — truncated at {_MAX_TEXT_CHARS:,} chars"
    if not text.strip():
        return _ok(f"{head}\n\n(empty body)")
    return _ok(f"{head}\n\n{text}")


@tool(
    "save_document",
    (
        "Save a document as a self-contained HTML file the user can open in "
        "Chrome and print to PDF (Ctrl/Cmd+P → Save as PDF). Use this when the "
        "user asks for a report, brief, note, or write-up as a file/PDF rather "
        "than just a chat reply. `title` is the document heading; `content` is "
        "the body in Markdown — headings, lists, tables, code, and LaTeX math "
        "using `\\(inline\\)` and `$$display$$` (rendered with KaTeX, no "
        "internet needed). Optional `filename` sets the file stem. The file is "
        "saved into the current project's files folder (or bran's documents "
        "folder outside a project); reply with the path so the user can open "
        "it. For a LaTeX .tex source instead, write one with the Write tool."
    ),
    {"title": str, "content": str, "filename": str},
)
async def save_document(args: dict[str, Any]) -> dict[str, Any]:
    from bran.background import current_project_id
    from bran.doc_export import DocAssetsMissing
    from bran.doc_export import save_document as _save

    title = (args.get("title") or "").strip()
    content = args.get("content") or ""
    if not title:
        return _err("a title is required.")
    if not content.strip():
        return _err("content is required (the document body, in Markdown).")
    filename = (args.get("filename") or "").strip() or None
    try:
        info = _save(
            title, content,
            project_id=current_project_id.get(),
            filename=filename,
        )
    except DocAssetsMissing as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"couldn't save the document: {e}")
    kb = info["size"] / 1024
    return _ok(
        f"Saved “{title}” → {info['path']} ({kb:,.0f} KB).\n"
        "Open it in Chrome and print to PDF (Ctrl/Cmd+P → Save as PDF), or find "
        "it in the project's Files. Tell the user the file name and where it is."
    )


# Document tools live on their own least-privilege server ("bran_docs" =>
# mcp__bran_docs__<name>) so utility agents (research, finance-news, summariser)
# can read PDFs / fetch feeds / save documents WITHOUT also getting the
# orchestration tools (spawn/runners) on the main "bran" server. `fetch_url` is
# granted only to agents that list it (finance-news), so it's not ambient.
DOCUMENT_TOOLS = [read_pdf, fetch_url, save_document]
documents_server = create_sdk_mcp_server(name="bran_docs", version="0.1.0", tools=DOCUMENT_TOOLS)
