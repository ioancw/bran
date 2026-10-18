"""One snapshot of "is bran able to work right now": the Claude login, the
integrations, the fleet limits, and spend. Rendered as the health strip on
Today, the banner that appears when the login is broken, and the read-only
Settings card.

The login check reads Claude Code's credentials file directly (no subprocess)
and corroborates it with the run history: a token can look valid and still
fail to refresh, and a run that just failed with a 401 is the truth.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bran.config import SETTINGS

_AUTH_FAILURE = ("401", "OAuth access token has expired", "Failed to authenticate")


def credentials_path() -> Path:
    return Path(os.getenv("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / ".credentials.json"


def _claude_login() -> dict[str, Any]:
    """{'method', 'ok', 'expires_at', 'subscription', 'detail'} without ever
    exposing a token."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return {"method": "api_key", "ok": True, "expires_at": None, "subscription": None, "detail": ""}
    path = credentials_path()
    if not path.exists():
        return {"method": "none", "ok": False, "expires_at": None, "subscription": None,
                "detail": "No Claude login found in this environment."}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"method": "claude.ai", "ok": False, "expires_at": None, "subscription": None,
                "detail": "The Claude credentials file could not be read."}
    oauth = data.get("claudeAiOauth") or {}
    exp_ms = oauth.get("expiresAt")
    expires_at = (
        datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc).isoformat() if isinstance(exp_ms, (int, float)) else None
    )
    out = {"method": "claude.ai", "ok": True, "expires_at": expires_at,
           "subscription": oauth.get("subscriptionType"), "detail": ""}
    # A past access-token expiry is normal (the CLI refreshes); a run that
    # failed with a 401 AFTER the last success is not.
    failure = _recent_auth_failure()
    if failure is not None:
        out["ok"] = False
        out["detail"] = f"The last run ({failure['started_at'][11:16]} UTC) failed to authenticate."
        out["failed_run_id"] = failure["id"]
    return out


def _recent_auth_failure() -> dict[str, Any] | None:
    """The newest run in the last 24h that failed to authenticate, unless a
    run has completed since (which proves the login works again)."""
    from bran.persistence import list_runs

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for r in list_runs(limit=40):
        if r.started_at < since:
            return None
        if r.status == "completed" and r.source != "subagent":
            return None
        text = (r.error or "") + (r.result or "")
        if r.status == "failed" and any(m in text for m in _AUTH_FAILURE):
            return {"id": r.id, "started_at": r.started_at}
    return None


def health_snapshot() -> dict[str, Any]:
    from bran.background import slots_in_use
    from bran.persistence import spend_summary

    login = _claude_login()
    spend = spend_summary()
    return {
        "login": login,
        "integrations": {
            "tavily": bool(os.getenv("TAVILY_API_KEY")),
            "webhook": bool(os.getenv("BRAN_NOTIFY_WEBHOOK_URL")),
        },
        "limits": {
            "run_budget_usd": SETTINGS.run_budget_usd,
            "run_timeout_s": SETTINGS.run_timeout_s,
            "max_concurrent_runs": SETTINGS.max_concurrent_runs,
            "max_spawn_depth": SETTINGS.max_spawn_depth,
            "subagent_depth": SETTINGS.subagent_depth,
            "subagent_concurrency": SETTINGS.subagent_concurrency,
            "runner_retries": SETTINGS.runner_retries,
            "default_model": SETTINGS.default_model,
        },
        "slots": {"in_use": slots_in_use(), "max": SETTINGS.max_concurrent_runs},
        "spend": spend,
        "paths": {
            "bran_home": str(SETTINGS.bran_home),
            "agents_dir": str(SETTINGS.project_root / "agents"),
        },
        "ok": login["ok"],
    }
