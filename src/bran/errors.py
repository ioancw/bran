"""Human-readable failure summaries.

Runs store the raw failure (traceback, CLI stderr tail) in `runs.error`, which
is right for debugging and wrong for a table cell. `summarize_failure` maps
the handful of failures that actually happen to one plain sentence and, where
there is something the user can do, a hint. Unknown failures fall back to the
exception's own first line, never a traceback.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from bran.config import SETTINGS


@dataclass(frozen=True)
class FailureSummary:
    title: str  # one sentence, no trailing period
    hint: str = ""  # what to do about it (may be empty)
    kind: str = "other"  # stable key for the UI: auth | budget | timeout | pruned | api | restart | process | other

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


_RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    # kind, pattern (searched in error + result), title, hint
    ("auth", re.compile(r"401|OAuth access token has expired|Failed to authenticate|authentication_error", re.I),
     "Claude login expired",
     "In a WSL terminal run `claude auth login`, then retry. bran's agents use WSL's login, not Windows'."),
    ("auth", re.compile(r"Invalid API key|invalid x-api-key", re.I),
     "Anthropic API key rejected", "Check ANTHROPIC_API_KEY in .env, then restart bran serve."),
    ("budget", re.compile(r"error_max_budget_usd|max_budget_usd|budget limit", re.I),
     "Stopped at the per-run budget", "Raise BRAN_RUN_BUDGET_USD or narrow the task."),
    ("timeout", re.compile(r"timed out after|RunTimeoutError", re.I),
     "Run timed out", "Raise BRAN_RUN_TIMEOUT, or split the task into smaller runs."),
    ("pruned", re.compile(r"No conversation found with session ID", re.I),
     "The conversation's transcript had been pruned",
     "Send the message again: bran now restores the conversation and continues."),
    ("api", re.compile(r"rate.?limit|overloaded|529|too many requests", re.I),
     "Anthropic API busy (rate limit or overload)", "Retry in a few minutes; scheduled runners retry on their own."),
    ("api", re.compile(r"socket connection was closed|ECONNRESET|Connection error|network", re.I),
     "Connection to the Anthropic API dropped", "Retry; if it persists, check the network from WSL."),
    ("restart", re.compile(r"Interrupted — the server restarted", re.I),
     "Interrupted by a server restart", "Run it again."),
    ("restart", re.compile(r"Parent run ended", re.I),
     "The parent run ended before this subagent returned", ""),
    ("cancelled", re.compile(r"Run cancelled|abandoned by client", re.I), "Cancelled", ""),
    ("process", re.compile(r"ended without a ResultMessage", re.I),
     "The agent process ended without a result", "Open the run and check the transcript's last step."),
    ("permission", re.compile(r"permission.*den|blocked by permissions|dontAsk", re.I),
     "A tool call was denied by permissions", "The agent asked for a tool its file doesn't grant."),
]

_CLI_STDERR = "--- CLI stderr (tail) ---"


def summarize_failure(error: str | None, result: str | None = None) -> FailureSummary | None:
    """A one-line summary for a failed/cancelled run, or None when there is
    nothing to summarise."""
    error = (error or "").strip()
    result = (result or "").strip()
    if not error and not result:
        return None
    haystack = error + "\n" + result
    for kind, pattern, title, hint in _RULES:
        if pattern.search(haystack):
            if kind == "budget":
                hint = f"Raise BRAN_RUN_BUDGET_USD (now ${SETTINGS.run_budget_usd:g}) or narrow the task."
            return FailureSummary(title=title, hint=hint, kind=kind)
    # Generic: prefer the last CLI stderr line, else the exception's own line.
    if _CLI_STDERR in error:
        tail = error.split(_CLI_STDERR, 1)[1].strip().splitlines()
        tail = [t for t in tail if t.strip()]
        if tail:
            return FailureSummary(title=_clip(tail[-1]), kind="process")
    first = _exception_line(error) or _clip(result.splitlines()[0] if result else "")
    return FailureSummary(title=first or "Failed", kind="other")


def _exception_line(error: str) -> str:
    """The `Name: message` line of a Python traceback, or the first line."""
    lines = [ln.strip() for ln in error.splitlines() if ln.strip()]
    if not lines:
        return ""
    for ln in reversed(lines):
        if ln.startswith(("File ", "Traceback", "---", "raise ", "^")):
            continue
        if ln.startswith("Exception: Claude Code returned an error result"):
            continue  # the CLI's generic wrapper — the real cause is elsewhere
        return _clip(ln)
    return _clip(lines[0])


def _clip(s: str, n: int = 140) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"
