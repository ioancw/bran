"""Where an answer came from.

The research-style agents cite as they go ("(Source: domain)") and list
their sources at the end; every page they fetched is also in the run's
events (WebFetch / fetch_url / tavily_extract calls). `extract_sources`
turns both into one de-duplicated list the UI can render as a Sources panel
and as inline citation chips — no change to the agents' prompts.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

FETCH_TOOLS = {"WebFetch", "mcp__bran_docs__fetch_url", "mcp__tavily__tavily_extract"}

_MD_LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\(\[])\bhttps?://[^\s<>\")\]]+")
_CITE = re.compile(r"\((?:Source|Sources):\s*([^)]{1,200})\)", re.I)
_TRAIL = ".,;:!?'\""


def domain_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _norm(url: str) -> str:
    url = url.strip().rstrip(_TRAIL)
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    path = parts.path.rstrip("/") or ""
    return f"{parts.scheme}://{(parts.hostname or '').lower()}{path}" + (f"?{parts.query}" if parts.query else "")


def _urls_from_input(inp: dict[str, Any]) -> list[str]:
    out: list[str] = []
    raw = inp.get("url") or inp.get("urls")
    if isinstance(raw, str):
        out += [u.strip() for u in re.split(r"[,\s]+", raw) if u.strip().startswith("http")]
    elif isinstance(raw, list):
        out += [str(u).strip() for u in raw if str(u).strip().startswith("http")]
    return out


def extract_sources(result: str | None, events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Sources for one run: cited ones (in the answer, in order) first, then
    pages the run fetched but didn't cite. Each: url, domain, title, via."""
    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def add(url: str, title: str | None, via: str) -> None:
        key = _norm(url)
        if not key or not domain_of(key):
            return
        if key in seen:
            if title and not seen[key]["title"]:
                seen[key]["title"] = title
            return
        seen[key] = {"url": url.strip().rstrip(_TRAIL), "domain": domain_of(key), "title": title, "via": via}
        order.append(key)

    text = result or ""
    for m in _MD_LINK.finditer(text):
        add(m.group(2), m.group(1).strip(), "cited")
    for m in _BARE_URL.finditer(text):
        add(m.group(0), None, "cited")

    fetched = [e for e in (events or []) if e.get("type") == "tool_use" and e.get("name") in FETCH_TOOLS]
    for e in fetched:
        for u in _urls_from_input(e.get("input") or {}):
            add(u, None, "fetched")

    # "(Source: bbc.co.uk)" with no URL anywhere → still worth listing.
    known = {s["domain"] for s in seen.values()}
    for m in _CITE.finditer(text):
        for dom in re.split(r"[,;]\s*|\s+and\s+", m.group(1)):
            dom = dom.strip().strip("`*_ ").lower()
            dom = dom[4:] if dom.startswith("www.") else dom
            if dom and "." in dom and " " not in dom and dom not in known:
                known.add(dom)
                seen[f"dom:{dom}"] = {"url": None, "domain": dom, "title": None, "via": "cited"}
                order.append(f"dom:{dom}")
    return [seen[k] for k in order]


def sources_for_run(run: Any) -> list[dict[str, Any]]:
    """Sources of a run including the subagents it delegated to (their fetches
    live on the child runs)."""
    from bran.persistence import list_runs

    events = list((run.metadata or {}).get("events") or [])
    for child in list_runs(parent_run_id=run.id, limit=50):
        events += (child.metadata or {}).get("events") or []
    return extract_sources(run.result, events)
