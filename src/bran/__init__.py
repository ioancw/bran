"""bran — a multi-agent fleet built on the Claude Agent SDK."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from bran.agents import Agent, get_agent, list_agents, register_agent
from bran.notify import install_default_notifiers, register_notifier
from bran.runner import run_agent, run_agent_sync

# Auto-install env-driven notifiers (webhook, console bell) on import so every
# entry point — CLI, REPL, library, HTTP server — gets them without bookkeeping.
install_default_notifiers()

__all__ = [
    "Agent",
    "run_agent",
    "run_agent_sync",
    "list_agents",
    "get_agent",
    "register_agent",
    "register_notifier",
]

# Single source of truth is pyproject's [project].version, read from installed
# package metadata. Falls back when running from a tree that isn't installed.
try:
    __version__ = _pkg_version("bran")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"
