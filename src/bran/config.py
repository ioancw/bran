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

    @property
    def claude_dir(self) -> Path:
        return self.project_root / ".claude"

    def ensure_dirs(self) -> None:
        """Create bran's data directories. Called lazily on first DB use (see
        persistence._ensure_ready) rather than at import, so merely importing
        bran has no filesystem side effects."""
        self.bran_home.mkdir(parents=True, exist_ok=True)
        self.briefings_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    root = _project_root()
    bran_home = Path(os.getenv("BRAN_HOME", str(root / ".bran"))).resolve()
    briefings_dir = bran_home / "briefings"
    return Settings(
        project_root=root,
        bran_home=bran_home,
        db_path=bran_home / "bran.sqlite",
        briefings_dir=briefings_dir,
        api_token=os.getenv("BRAN_API_TOKEN") or None,
        host=os.getenv("BRAN_HOST", "127.0.0.1"),
        port=int(os.getenv("BRAN_PORT", "8765")),
        default_model=os.getenv("BRAN_DEFAULT_MODEL", "sonnet"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        max_buffer_size=int(os.getenv("BRAN_MAX_BUFFER_SIZE", str(10 * 1024 * 1024))),
    )


SETTINGS = load_settings()
