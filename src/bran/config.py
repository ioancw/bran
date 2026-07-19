"""Runtime configuration: paths, env vars, model defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _project_root() -> Path:
    # src/bran/config.py -> src/bran -> src -> project root
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    project_root: Path
    bran_home: Path
    db_path: Path
    briefings_dir: Path
    api_token: str | None
    # Named bearer tokens for the /api surface: {token: name}. Set via
    # BRAN_API_TOKENS="ioan:tok1,partner:tok2". BRAN_API_TOKEN (singular) is
    # folded in under the name "api". Runs triggered over /api are attributed
    # to the token's name (runs.actor) so a small team can share one server.
    api_tokens: dict[str, str]
    host: str
    port: int
    default_model: str
    anthropic_api_key: str | None
    # Max bytes for a single SDK stdout JSON message. The SDK's own default is
    # 1MB (claude_agent_sdk subprocess transport), which a single large tool
    # result — a fetched web page, a big file read — can blow past, aborting
    # the run mid-stream. The cookbooks bump this for web-research agents; we
    # default to 10MB. Override with BRAN_MAX_BUFFER_SIZE (bytes).
    max_buffer_size: int
    # Wall-clock ceiling for a single agent run, in seconds. A hung SDK
    # subprocess (stuck tool call, network stall) would otherwise block its
    # asyncio task forever and back up the scheduler. 0 disables the limit.
    # Override with BRAN_RUN_TIMEOUT (seconds).
    run_timeout_s: int
    # How many times a failed *scheduled* run is retried (with backoff) before
    # giving up until the next regular fire. Override with BRAN_RUNNER_RETRIES.
    runner_retries: int
    # Host headers the server will answer to. Anything else is rejected with a
    # 400 — this is the DNS-rebinding defence for the token-free /spa surface
    # (a page on evil.com whose DNS is rebound to 127.0.0.1 would otherwise
    # pass the same-origin check). Extend with BRAN_ALLOWED_HOSTS="lanbox,..."
    # when binding beyond localhost.
    allowed_hosts: tuple[str, ...]

    @property
    def claude_dir(self) -> Path:
        return self.project_root / ".claude"

    def ensure_dirs(self) -> None:
        """Create bran's data directories. Called lazily on first DB use (see
        persistence._ensure_ready) rather than at import, so merely importing
        bran has no filesystem side effects."""
        self.bran_home.mkdir(parents=True, exist_ok=True)
        self.briefings_dir.mkdir(parents=True, exist_ok=True)


def _parse_api_tokens() -> dict[str, str]:
    """{token: name} from BRAN_API_TOKENS ('name:token,name2:token2'), plus the
    legacy single BRAN_API_TOKEN under the name 'api'. Malformed entries are
    skipped rather than crashing startup."""
    out: dict[str, str] = {}
    single = os.getenv("BRAN_API_TOKEN")
    if single:
        out[single] = "api"
    for entry in (os.getenv("BRAN_API_TOKENS") or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, _, token = entry.partition(":")
        if name.strip() and token.strip():
            out[token.strip()] = name.strip()
    return out


def _parse_allowed_hosts(bind_host: str) -> tuple[str, ...]:
    """Hostnames (no port) accepted in the Host header. Loopback names are
    always allowed; the bind host is included so a LAN bind works out of the
    box once BRAN_HOST is set to a concrete address."""
    hosts = {"127.0.0.1", "localhost", "::1", bind_host}
    hosts.discard("0.0.0.0")  # never a real Host header — don't allowlist it
    for entry in (os.getenv("BRAN_ALLOWED_HOSTS") or "").split(","):
        entry = entry.strip()
        if entry:
            hosts.add(entry)
    return tuple(sorted(hosts))


def _warn_if_windows_mount(bran_home: Path) -> None:
    """SQLite WAL over WSL's /mnt/* (9p/drvfs) is a documented corruption and
    latency hazard — the DB belongs on Linux-native ext4."""
    if os.name == "posix" and str(bran_home).startswith("/mnt/"):
        import logging

        logging.getLogger("bran.config").warning(
            "BRAN_HOME (%s) is on a Windows-mounted filesystem. SQLite in WAL "
            "mode is unreliable and slow over /mnt/*: set BRAN_HOME to a "
            "Linux-native path (e.g. ~/.bran) and copy the data over.",
            bran_home,
        )


def load_settings() -> Settings:
    root = _project_root()
    bran_home = Path(os.getenv("BRAN_HOME", str(root / ".bran"))).resolve()
    _warn_if_windows_mount(bran_home)
    briefings_dir = bran_home / "briefings"
    return Settings(
        project_root=root,
        bran_home=bran_home,
        db_path=bran_home / "bran.sqlite",
        briefings_dir=briefings_dir,
        api_token=os.getenv("BRAN_API_TOKEN") or None,
        api_tokens=_parse_api_tokens(),
        host=os.getenv("BRAN_HOST", "127.0.0.1"),
        port=int(os.getenv("BRAN_PORT", "8765")),
        default_model=os.getenv("BRAN_DEFAULT_MODEL", "sonnet"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        max_buffer_size=int(os.getenv("BRAN_MAX_BUFFER_SIZE", str(10 * 1024 * 1024))),
        run_timeout_s=int(os.getenv("BRAN_RUN_TIMEOUT", "3600")),
        runner_retries=int(os.getenv("BRAN_RUNNER_RETRIES", "2")),
        allowed_hosts=_parse_allowed_hosts(os.getenv("BRAN_HOST", "127.0.0.1")),
    )


SETTINGS = load_settings()
