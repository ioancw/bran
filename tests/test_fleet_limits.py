"""Fleet bounds: per-run budget and turn ceilings, the deny list that keeps
ungranted built-ins out of reach, no user-level settings in agent runs, the
background-run concurrency gate, the spawn depth limit, per-session locks,
and @mention delegation staying inside the conversation."""

from __future__ import annotations

import asyncio
import uuid

from bran.agents import BUILTIN_TOOLS, build_options_for, deny_list_for, get_agent, list_agents
from bran.background import current_run_id, run_slots, session_lock
from bran.config import SETTINGS
from bran.persistence import RunRecord, get_run, insert_run

# ---------------------------------------------------------------------------
# SDK options
# ---------------------------------------------------------------------------

def test_every_agent_has_a_turn_ceiling_and_a_budget():
    for a in list_agents():
        opts = build_options_for(a)
        assert opts.max_turns is not None and opts.max_turns > 0, a.name
        assert opts.max_budget_usd == SETTINGS.run_budget_usd
        assert opts.permission_mode == "dontAsk"
    # Subagent definitions carry their own ceilings.
    for sub in get_agent("orchestrator").subagents.values():
        assert sub.maxTurns is not None and sub.maxTurns > 0


def test_user_settings_never_load_into_agent_runs():
    opts = build_options_for(get_agent("orchestrator"))
    assert "user" not in opts.setting_sources
    assert "project" in opts.setting_sources


def test_deny_list_strips_ungranted_builtins():
    for a in list_agents():
        opts = build_options_for(a)
        denied = set(opts.disallowed_tools)
        assert "Bash" in denied, a.name  # no bran agent ever gets a shell
        for t in a.tools:
            assert t not in denied, (a.name, t)
    # Granting Agent keeps its CLI alias Task too.
    assert "Task" not in deny_list_for(["Agent"])
    assert set(deny_list_for([])) == set(BUILTIN_TOOLS)


def test_orchestrator_does_not_browse_the_web_itself():
    o = get_agent("orchestrator")
    assert "WebFetch" not in o.tools and "WebSearch" not in o.tools
    assert "WebFetch" in build_options_for(o).disallowed_tools


def test_inline_subagent_caps_travel_in_env():
    env = build_options_for(get_agent("research")).env
    assert env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == str(SETTINGS.subagent_depth)
    assert env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == str(SETTINGS.subagent_concurrency)


# ---------------------------------------------------------------------------
# Concurrency gate
# ---------------------------------------------------------------------------

def test_background_runs_queue_behind_the_slot_gate(monkeypatch):
    """With one slot, the second background run stays 'pending' until the
    first finishes; a chat turn is never gated."""
    import bran.runner as runner_mod
    from bran.runner import run_agent

    object.__setattr__(runner_mod.SETTINGS, "max_concurrent_runs", 1)
    started: list[str] = []
    release = None

    class ResultMessage:
        def __init__(self):
            self.num_turns = 1
            self.total_cost_usd = 0.0
            self.session_id = "s"
            self.subtype = "success"
            self.is_error = False
            self.result = "ok"

    async def slow_query(prompt, options):
        started.append(prompt)
        await release.wait()
        yield ResultMessage()

    monkeypatch.setattr(runner_mod, "query", slow_query)

    async def go():
        nonlocal release
        release = asyncio.Event()
        import bran.background as bg
        bg._slots = None  # fresh gate for this loop + setting
        t1 = asyncio.create_task(run_agent("research", "first", source="spawn"))
        await asyncio.sleep(0.05)
        t2 = asyncio.create_task(run_agent("research", "second", source="spawn"))
        await asyncio.sleep(0.05)
        assert started == ["first"]  # second is queued, not running
        from bran.persistence import list_runs
        pending = [r for r in list_runs(agent="research", status="pending", limit=5) if r.task == "second"]
        assert pending, "queued run should sit 'pending'"
        release.set()
        r1, r2 = await asyncio.gather(t1, t2)
        assert started == ["first", "second"]
        # The stand-in result isn't the SDK class, so the driver can't mark
        # these completed; what matters here is that both ran to a terminal
        # state, strictly one after the other.
        assert r1.status in ("completed", "failed") and r2.status in ("completed", "failed")

    try:
        asyncio.run(go())
    finally:
        object.__setattr__(runner_mod.SETTINGS, "max_concurrent_runs", 4)


def test_run_slots_rebuild_per_loop():
    async def one():
        return run_slots()

    a = asyncio.run(one())
    b = asyncio.run(one())
    assert a is not b


# ---------------------------------------------------------------------------
# Spawn depth
# ---------------------------------------------------------------------------

def test_spawn_refuses_beyond_max_depth():
    from bran.tools.spawn import spawn_agent

    parent = RunRecord.new(agent="orchestrator", task="deep", source="spawn")
    parent.metadata["depth"] = SETTINGS.max_spawn_depth  # already at the floor
    parent.status = "running"
    insert_run(parent)

    async def go():
        token = current_run_id.set(parent.id)
        try:
            # The underlying SDK tool wrapper exposes the handler; call it directly.
            handler = getattr(spawn_agent, "handler", None) or spawn_agent
            return await handler({"agent": "research", "task": "go deeper", "wait": False})
        finally:
            current_run_id.reset(token)

    out = asyncio.run(go())
    text = out["content"][0]["text"]
    assert text.startswith("Error:") and "depth" in text


def test_spawn_records_its_depth(monkeypatch):
    from bran.tools.spawn import spawn_agent

    async def fake_run_agent(agent, task, record=None, **kw):
        record.status = "completed"
        return record

    import bran.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_agent", fake_run_agent)

    parent = RunRecord.new(agent="orchestrator", task="turn", source="chat")
    parent.status = "running"
    insert_run(parent)

    async def go():
        token = current_run_id.set(parent.id)
        try:
            handler = getattr(spawn_agent, "handler", None) or spawn_agent
            return await handler({"agent": "research", "task": "child", "wait": True})
        finally:
            current_run_id.reset(token)

    out = asyncio.run(go())
    rid = out["content"][0]["text"].split("run ")[1].split()[0].rstrip("]")
    child = get_run(rid)
    assert child is not None and child.metadata["depth"] == 1


# ---------------------------------------------------------------------------
# Session lock
# ---------------------------------------------------------------------------

def test_session_lock_is_per_session_and_exclusive():
    async def go():
        a1 = session_lock("A")
        a2 = session_lock("A")
        b = session_lock("B")
        assert a1 is a2 and a1 is not b
        async with a1:
            assert a1.locked() and not b.locked()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# @mention delegation
# ---------------------------------------------------------------------------

def test_delegation_prompt_names_the_subagent():
    from bran.web.spa_api import delegation_prompt

    p = delegation_prompt("research", "what is the BoE base rate?")
    assert "`research` subagent" in p and p.endswith("what is the BoE base rate?")


def test_mention_in_orchestrator_chat_keeps_the_session(monkeypatch):
    """An @research message in an orchestrator chat resumes the SAME session
    with a delegation prompt, instead of starting a new chat with research."""
    from fastapi.testclient import TestClient

    import bran.web.spa_api as spa
    from bran.api import build_app
    from bran.persistence import ChatRecord, upsert_chat

    sid = "sess-" + uuid.uuid4().hex
    upsert_chat(ChatRecord(id=sid, title="t", agent="orchestrator"))
    seen: dict = {}

    async def fake_stream(agent, prompt, *, resume_session=None, **kw):
        seen.update(agent=agent, prompt=prompt, resume=resume_session)
        return
        yield  # pragma: no cover

    monkeypatch.setattr(spa, "stream_agent", fake_stream)
    with TestClient(build_app(enable_scheduler=False)) as c:
        r = c.post("/spa/chat/stream", data={"prompt": "@research find the rate", "session_id": sid})
        assert r.status_code == 200
    assert seen["agent"] == "orchestrator"
    assert seen["resume"] == sid
    assert "`research` subagent" in seen["prompt"] and seen["prompt"].endswith("find the rate")
    assert '"routed"' in r.text and '"research"' in r.text
