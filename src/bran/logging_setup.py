"""Root logging configuration for the long-running `bran serve` process.

Without this, `logging.getLogger("bran.*")` messages (scheduler fires, retry
backoff, run reconciliation) go to Python's lastResort handler, which drops
everything below WARNING and never persists past the terminal buffer — so a
runner that misfired at 8am leaves no forensic trail. `uvicorn.run` configures
only uvicorn's own loggers, not the root, so we set up our own handlers.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from bran.config import SETTINGS

_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attach a console handler + a rotating file handler to the root logger.

    Idempotent — safe to call more than once (e.g. serve + a background task).
    The file lives at ``$BRAN_HOME/logs/bran.log`` (5 files × 5 MB).
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        log_dir = SETTINGS.bran_home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "bran.log", maxBytes=5 * 1024 * 1024, backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # A read-only or missing home shouldn't stop the server from starting;
        # console logging still works.
        logging.getLogger("bran").warning("could not open log file — console only")

    _configured = True
