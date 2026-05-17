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

    @property
    def claude_dir(self) -> Path:
        return self.project_root / ".claude"


def load_settings() -> Settings:
    root = _project_root()
    bran_home = Path(os.getenv("BRAN_HOME", str(root / ".bran"))).resolve()
    bran_home.mkdir(parents=True, exist_ok=True)
    briefings_dir = bran_home / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
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
    )


SETTINGS = load_settings()
