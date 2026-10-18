"""Draft a runner from one sentence.

"every weekday at 7, brief me on FX and fact-check it" → a filled-in runner
form (name, agent, schedule, task, flags) for the user to confirm. A cheap,
tool-less model call picks the agent from the roster and phrases the task;
the schedule goes through the same natural-language parser the form uses,
so what the user sees is exactly what would be saved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

log = logging.getLogger(__name__)

_TIMEOUT_S = 60


def _prompt(text: str, roster: list[tuple[str, str]], projects: list[str]) -> str:
    agents = "\n".join(f"- {name}: {desc}" for name, desc in roster)
    projs = ", ".join(projects) if projects else "(none)"
    now = datetime.now().astimezone()
    return (
        "Turn the user's sentence into a bran runner: an agent that runs on a schedule.\n\n"
        f"Today is {now.strftime('%A %d %B %Y, %H:%M')} (local time).\n\n"
        f"Agents available (pick the best fit by description; default orchestrator):\n{agents}\n\n"
        f"Projects the runner may be attached to (only if the sentence clearly names one): {projs}\n\n"
        "Reply with ONE JSON object and nothing else:\n"
        "{\n"
        '  "name": "short-kebab-case-name",\n'
        '  "agent": "<agent name>",\n'
        '  "kind": "cron" | "once",\n'
        '  "schedule": "<when, in plain words, e.g. every weekday at 7am / every Monday at 8:30 / every 2 hours>",\n'
        '  "run_at": "<ISO-8601 local datetime, only when kind is once, else empty>",\n'
        '  "task": "<what the agent should do each run, 1–3 sentences, imperative, no schedule words>",\n'
        '  "project": "<exact project name or empty>",\n'
        '  "verify": true | false,   // the user wants outputs checked / verified / fact-checked\n'
        '  "delta": true | false,    // the user wants only what changed since last time\n'
        '  "alert": "<a significance bar in plain words, only if the user wants to be alerted when something happens, else empty>"\n'
        "}\n\n"
        f"User's sentence: {text.strip()}"
    )


def _options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt="You turn a sentence into a JSON runner definition. You only ever reply with the JSON object.",
        allowed_tools=[],
        disallowed_tools=["*"],
        setting_sources=[],
        permission_mode="dontAsk",
        model="haiku",
        max_turns=1,
        max_budget_usd=0.2,
    )


async def _ask(prompt: str) -> str:
    out = ""
    async for msg in query(prompt=prompt, options=_options()):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    out += block.text
    return out


def _kebab(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:40] or "runner"


async def draft_runner(text: str) -> dict[str, Any]:
    """The draft, as form fields. Never raises on a parse problem: a schedule
    the parser rejects comes back with `cron` empty and `schedule_error` set,
    so the form can show the user what to fix."""
    from bran.agents import list_agents
    from bran.persistence import list_projects

    roster = [(a.name, a.description) for a in list_agents()]
    projects = list_projects()
    raw = await asyncio.wait_for(_ask(_prompt(text, roster, [p.name for p in projects])), timeout=_TIMEOUT_S)
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError("The model did not return a runner definition.")
    data = json.loads(m.group(0))

    names = {a.name for a in list_agents()}
    agent = str(data.get("agent") or "").strip()
    if agent not in names:
        agent = "orchestrator"
    kind = "once" if str(data.get("kind") or "").lower() == "once" else "cron"
    schedule = str(data.get("schedule") or "").strip()
    cron, human, schedule_error = "", "", ""
    run_at = ""
    if kind == "once":
        candidate = str(data.get("run_at") or "").strip()
        try:
            run_at = datetime.fromisoformat(candidate).isoformat(timespec="minutes")
        except ValueError:
            kind = "cron"  # fall back to a schedule the user can edit
    if kind == "cron":
        from bran.nl_cron import NlCronParseError, parse

        try:
            r = parse(schedule or "every day at 8am")
            cron, human = r.cron, r.human
        except NlCronParseError as e:
            schedule_error = str(e)
    project_name = str(data.get("project") or "").strip().lower()
    project_id = next((p.id for p in projects if p.name.lower() == project_name), "") if project_name else ""
    return {
        "name": _kebab(str(data.get("name") or "")),
        "agent": agent,
        "kind": kind,
        "schedule": schedule,
        "cron": cron,
        "human": human,
        "schedule_error": schedule_error,
        "run_at": run_at,
        "task": str(data.get("task") or "").strip(),
        "project_id": project_id,
        "verify": bool(data.get("verify")),
        "delta": bool(data.get("delta")),
        "alert": str(data.get("alert") or "").strip(),
    }
