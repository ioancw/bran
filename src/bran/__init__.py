"""bran — a multi-agent fleet built on the Claude Agent SDK."""

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
__version__ = "0.1.0"
