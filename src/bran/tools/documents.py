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

import io
import os
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

_MAX_PAGES = 50
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB
_TIMEOUT_S = 30.0


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
    import httpx

    headers = {"User-Agent": "bran/0.1 (+read_pdf)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT_S, headers=headers) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise ValueError(f"HTTP {resp.status_code} fetching {url}")
    if len(resp.content) > _MAX_BYTES:
        raise ValueError(f"PDF is {len(resp.content):,} bytes — exceeds the {_MAX_BYTES:,} byte limit")
    return resp.content


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


# Document tools live on their own least-privilege server ("bran_docs" =>
# mcp__bran_docs__<name>) so utility agents (research, finance-news, summariser)
# can read PDFs WITHOUT also getting the orchestration tools (spawn/runners) on
# the main "bran" server.
DOCUMENT_TOOLS = [read_pdf]
documents_server = create_sdk_mcp_server(name="bran_docs", version="0.1.0", tools=DOCUMENT_TOOLS)
