"""The health strip (login, integrations, limits, spend) and the humanised
failure summaries that replace tracebacks in the UI."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import bran.health as health_mod
from bran.api import build_app
from bran.errors import summarize_failure
from bran.health import health_snapshot
from bran.persistence import RunRecord, insert_run, spend_summary, utcnow_iso

# ---------------------------------------------------------------------------
# Failure summaries
# ---------------------------------------------------------------------------

def test_known_failures_become_sentences():
    s = summarize_failure("Traceback…\nException: Claude Code returned an error result: success",
                          "Failed to authenticate. API Error: 401 OAuth access token has expired.")
    assert s.kind == "auth" and s.title == "Claude login expired" and "claude auth login" in s.hint
    assert summarize_failure("ProcessError …\n--- CLI stderr (tail) ---\nNo conversation found with session ID: x").kind == "pruned"
    b = summarize_failure("error_max_budget_usd")
    assert b.kind == "budget" and "BRAN_RUN_BUDGET_USD" in b.hint
    assert summarize_failure("Run timed out after 3600s (BRAN_RUN_TIMEOUT)").kind == "timeout"
    assert summarize_failure("Interrupted — the server restarted before this run finished.").kind == "restart"
    assert summarize_failure("Run cancelled").title == "Cancelled"
    assert summarize_failure("Agent stream ended without a ResultMessage").kind == "process"


def test_unknown_failure_uses_the_exception_line_not_the_traceback():
    err = (
        "ValueError: something odd\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "    raise ValueError('something odd')\n"
        "ValueError: something odd"
    )
    s = summarize_failure(err)
    assert s.title == "ValueError: something odd" and s.kind == "other"
    assert "Traceback" not in s.title and "File" not in s.title


def test_generic_cli_failure_uses_last_stderr_line():
    s = summarize_failure("ProcessError: exit 1\n--- CLI stderr (tail) ---\nfirst line\nnpm ERR! cannot find tavily-mcp")
    assert s.title == "npm ERR! cannot find tavily-mcp" and s.kind == "process"


def test_nothing_to_summarise():
    assert summarize_failure(None, None) is None
    assert summarize_failure("", "") is None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def _creds(tmp_path: Path, expires_in_s: float) -> Path:
    p = tmp_path / ".credentials.json"
    p.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "sk-ant-oat01-secret", "refreshToken": "r", "scopes": ["user:inference"],
        "expiresAt": int((time.time() + expires_in_s) * 1000), "subscriptionType": "max",
    }}), encoding="utf-8")
    return p


def test_health_reports_a_valid_login_without_leaking_tokens(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(health_mod, "credentials_path", lambda: _creds(tmp_path, 3600))
    h = health_snapshot()
    assert h["login"]["method"] == "claude.ai" and h["login"]["ok"] is True
    assert h["login"]["subscription"] == "max" and h["login"]["expires_at"]
    assert "secret" not in json.dumps(h)
    assert set(h["limits"]) >= {"run_budget_usd", "max_concurrent_runs", "max_spawn_depth", "run_timeout_s"}
    assert h["slots"]["max"] >= 1 and "today" in h["spend"] and "runners_30d" in h["spend"]


def test_health_flags_a_recent_auth_failure(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(health_mod, "credentials_path", lambda: _creds(tmp_path, 3600))
    r = RunRecord.new(agent="summariser", task="t", source="manual")
    r.status = "failed"
    r.result = "Failed to authenticate. API Error: 401 OAuth access token has expired."
    r.started_at = utcnow_iso()
    insert_run(r)
    h = health_snapshot()
    assert h["login"]["ok"] is False and h["login"]["failed_run_id"] == r.id
    assert h["ok"] is False
    # A later successful run clears it.
    ok = RunRecord.new(agent="summariser", task="t", source="manual")
    ok.status = "completed"
    ok.result = "ok"
    ok.started_at = utcnow_iso()
    insert_run(ok)
    assert health_snapshot()["login"]["ok"] is True


def test_health_with_api_key_and_with_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert health_snapshot()["login"]["method"] == "api_key"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(health_mod, "credentials_path", lambda: tmp_path / "missing.json")
    h = health_snapshot()
    assert h["login"]["method"] == "none" and h["login"]["ok"] is False


def test_spend_summary_counts_windows_and_runners():
    sched = "sched-" + uuid.uuid4().hex
    r = RunRecord.new(agent="finance-news", task="t", source="runner", schedule_id=sched)
    r.status = "completed"
    r.total_cost_usd = 0.5
    r.started_at = utcnow_iso()
    insert_run(r)
    s = spend_summary()
    assert s["today"]["cost"] >= 0.5 and s["week"]["runs"] >= 1 and s["month"]["cost"] >= s["today"]["cost"]
    assert s["runners_30d"][sched] == {"cost": 0.5, "runs": 1}


def test_endpoints_expose_health_and_error_summaries():
    r = RunRecord.new(agent="summariser", task="t", source="manual")
    r.status = "failed"
    r.error = "Run timed out after 10s (BRAN_RUN_TIMEOUT)."
    insert_run(r)
    with TestClient(build_app(enable_scheduler=False)) as c:
        h = c.get("/spa/health").json()
        assert "login" in h and "limits" in h and "spend" in h
        d = c.get(f"/spa/runs/{r.id}").json()
        assert d["error_summary"]["kind"] == "timeout"
        rows = [x for x in c.get("/spa/runs?limit=50").json() if x["id"] == r.id]
        assert rows and rows[0]["error_summary"]["title"] == "Run timed out"
