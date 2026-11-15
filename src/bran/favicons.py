"""Site icons for the sources an answer cites.

The UI shows each source with its site's icon. Asking a third-party icon
service would hand it the list of every site the agents read, so bran fetches
`https://<domain>/favicon.ico` itself — a request to the site the answer
already names, through the same egress guard as `fetch_url` — and keeps the
result on disk. Anything that is not a small raster image is discarded; the
UI falls back to a lettered tile.

Set BRAN_FAVICONS=0 to make no such requests at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bran.config import SETTINGS

log = logging.getLogger(__name__)

_DOMAIN = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_MAX_BYTES = 256 * 1024
_TIMEOUT_S = 6
_MAX_REDIRECTS = 3
_HIT_TTL_S = 30 * 86400
_MISS_TTL_S = 3 * 86400
# Second-level labels that are registries, not sites ("bbc.co.uk" must not be
# shortened to "co.uk" when looking for a parent site's icon).
_REGISTRY_LABELS = {"co", "com", "org", "net", "gov", "ac", "edu"}

_in_flight: dict[str, asyncio.Task[tuple[bytes, str] | None]] = {}


def enabled() -> bool:
    return os.getenv("BRAN_FAVICONS", "1") != "0"


def valid_domain(domain: str) -> bool:
    return bool(_DOMAIN.match(domain))


def image_type(data: bytes) -> str | None:
    """The media type of a raster image, by its leading bytes; None for
    anything else (HTML error pages, SVG — which can carry script)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def candidates(domain: str) -> list[str]:
    """Hosts to try, most specific first: "feeds.bbci.co.uk" has no icon of
    its own, "bbci.co.uk" does."""
    out = [domain]
    labels = domain.split(".")
    while len(labels) > 2:
        labels = labels[1:]
        if len(labels) == 2 and labels[0] in _REGISTRY_LABELS:
            break
        out.append(".".join(labels))
    return out[:3]


def _cache_dir() -> Path:
    d = SETTINGS.bran_home / "cache" / "favicons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached(domain: str) -> tuple[bool, tuple[bytes, str] | None]:
    """(known, icon): known is False when there is nothing fresh on disk."""
    hit = _cache_dir() / f"{domain}.img"
    miss = _cache_dir() / f"{domain}.none"
    now = time.time()
    try:
        if hit.is_file() and now - hit.stat().st_mtime < _HIT_TTL_S:
            data = hit.read_bytes()
            kind = image_type(data)
            if kind:
                return True, (data, kind)
        if miss.is_file() and now - miss.stat().st_mtime < _MISS_TTL_S:
            return True, None
    except OSError:
        pass
    return False, None


def _store(domain: str, icon: tuple[bytes, str] | None) -> None:
    hit = _cache_dir() / f"{domain}.img"
    miss = _cache_dir() / f"{domain}.none"
    try:
        if icon is None:
            hit.unlink(missing_ok=True)
            miss.write_bytes(b"")
        else:
            miss.unlink(missing_ok=True)
            hit.write_bytes(icon[0])
    except OSError:
        log.debug("could not cache the icon of %s", domain, exc_info=True)


async def _download(url: str) -> bytes | None:
    """GET one small file through the egress guard; None on any failure."""
    import httpx

    from bran.tools.documents import _pinned_request, _vetted_address

    headers = {"User-Agent": "Mozilla/5.0 (compatible; bran)", "Accept": "image/*"}
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT_S, headers=headers) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                if not url.startswith(("http://", "https://")):
                    return None
                target, pin_headers, extensions = _pinned_request(url, await _vetted_address(url))
                async with client.stream("GET", target, headers=pin_headers, extensions=extensions) as resp:
                    if resp.is_redirect:
                        url = urljoin(url, resp.headers.get("location", ""))
                        continue
                    if resp.status_code >= 400:
                        return None
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            return None
                        chunks.append(chunk)
                    return b"".join(chunks)
    except (ValueError, httpx.HTTPError, OSError):
        return None
    return None


async def _fetch(domain: str) -> tuple[bytes, str] | None:
    for host in candidates(domain):
        data = await _download(f"https://{host}/favicon.ico")
        kind = image_type(data) if data else None
        if data and kind:
            return data, kind
    return None


async def favicon_for(domain: str) -> tuple[bytes, str] | None:
    """(bytes, media type) of the site's icon, or None when it has none worth
    showing. Fetched at most once per TTL; concurrent requests share one fetch."""
    domain = domain.strip().lower()
    if not enabled() or not valid_domain(domain):
        return None
    known, icon = _cached(domain)
    if known:
        return icon
    task = _in_flight.get(domain)
    if task is None:
        task = asyncio.ensure_future(_fetch(domain))
        _in_flight[domain] = task
        task.add_done_callback(lambda _t: _in_flight.pop(domain, None))
    try:
        icon = await asyncio.shield(task)
    except Exception:  # noqa: BLE001 — an icon is never worth an error
        log.debug("icon fetch failed for %s", domain, exc_info=True)
        icon = None
    _store(domain, icon)
    return icon
