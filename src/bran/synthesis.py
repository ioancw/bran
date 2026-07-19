"""Headless fan-in: synthesise a chat's fan-out once every spawn finishes.

Historically the ONLY thing that merged a fan-out's results back into one
answer was a watcher in the chat page — close the tab and the finished runs
just sat there as separate reports. This module moves ownership to the server:
`runner._drive` calls `check_after_run` for every finished run, and when the
last member of a batch lands, the server itself runs the synthesis turn —
resuming the originating chat session so the combined answer appears in that
conversation, lands in Outputs, and (being a background source) pushes to the
phone via the notify webhook.

Batch model: a chat turn's run id is the `parent_run_id` of every run it
spawned. A batch qualifies when the parent chat turn is finished (so no more
spawns are coming), it contains >=2 non-inline background spawns (wait=true
spawns are consumed inside the turn that made them), and all spawns are
terminal. `claim_fanout_synthesis` guarantees exactly one synthesis per batch
no matter how many sibling completions race the check.

The open-tab UX is unchanged in spirit: the chat page now *watches for* the
server's synthesis run and refreshes the conversation when it lands, instead
of submitting the canned message itself.
"""

from __future__ import annotations

import logging

from bran.background import spawn_background
from bran.persistence import (
    RunRecord,
    claim_fanout_synthesis,
    get_chat,
    get_run,
    list_spawns,
    touch_chat,
)

log = logging.getLogger("bran.synthesis")

TERMINAL = ("completed", "failed", "cancelled")

# Matches the significance framing the orchestrator's own prompt uses for
# fan-in synthesis (agents.py) — cluster first, corroborate, rank.
_PROMPT_HEAD = (
    "Your earlier fan-out has finished — the background runs below are done. "
    "Collect each completed run's output with `mcp__bran__get_run_result`, then "
    "synthesise everything into ONE combined answer for the user. Cluster "
    "overlapping findings first and report each cluster once (noting "
    "corroboration, e.g. '3 of 4 sources'), reconcile contradictions "
    "explicitly, and rank clusters by significance — do not summarise run by "
    "run. Address the user directly; they asked for this work earlier and are "
    "now reading the combined result."
)


def _synthesis_prompt(spawns: list[RunRecord]) -> str:
    lines = [_PROMPT_HEAD, ""]
    for s in spawns:
        if s.status == "completed":
            lines.append(f"- run {s.id} ({s.agent}): completed — {s.task[:120]}")
        else:
            lines.append(
                f"- run {s.id} ({s.agent}): {s.status} — {s.task[:120]} "
                "(no result; note the gap briefly, don't invent content)"
            )
    return "\n".join(lines)


async def maybe_synthesise(parent_run_id: str) -> str | None:
    """Synthesise the batch under `parent_run_id` if it is ready and unclaimed.

    Returns the synthesis run's id when one was started, else None. Ordering of
    the readiness guards matters: everything cheap and repeatable happens
    BEFORE the one-shot claim, so an unready batch can be re-checked by a later
    completion without burning the claim.
    """
    parent = get_run(parent_run_id)
    if parent is None or parent.status not in TERMINAL:
        return None  # turn still running — more spawns may be coming
    # Only chat turns get headless synthesis: the combined answer needs a
    # conversation to land in. (Runner/manual orchestrator runs collect their
    # own results inside the run, or the user reads them in Outputs.)
    if parent.source != "chat" or not parent.session_id:
        return None
    chat = get_chat(parent.session_id)
    if chat is None:
        return None

    spawns = [s for s in list_spawns(parent_run_id) if not s.metadata.get("inline")]
    if len(spawns) < 2:
        return None  # a lone background run isn't a fan-out
    if any(s.status not in TERMINAL for s in spawns):
        return None  # batch still in flight — the last finisher re-checks

    if not claim_fanout_synthesis(parent_run_id):
        return None  # someone else (a racing sibling) owns it

    completed = [s for s in spawns if s.status == "completed"]
    if not completed:
        # Claim consumed on purpose: an all-failed batch has nothing to merge,
        # and re-running the check later can't change that.
        log.info("fan-out %s: all %d spawns failed — skipping synthesis",
                 parent_run_id, len(spawns))
        return None

    log.info("fan-out %s complete (%d/%d ok) — starting headless synthesis",
             parent_run_id, len(completed), len(spawns))

    # Lazy imports break the runner <-> synthesis cycle and keep APScheduler
    # out of this module's import path.
    from bran.runner import run_agent
    from bran.scheduler import _project_append_system

    run = await run_agent(
        chat.agent,
        _synthesis_prompt(spawns),
        resume_session=chat.id,
        parent_run_id=parent_run_id,
        project_id=chat.project_id,
        source="synthesis",
        append_system=_project_append_system(chat.project_id),
    )
    touch_chat(chat.id)
    return run.id


def suppresses_notification(record: RunRecord) -> bool:
    """True for a completed spawn that belongs to a synthesised batch — its
    webhook push is redundant (and a 5-run fan-out would ping the phone six
    times). The synthesis run pushes the combined answer instead. Failed
    spawns still notify: if the whole batch fails, no synthesis fires, and
    silence would hide the failure."""
    if record.source != "spawn" or record.status != "completed":
        return False
    if record.metadata.get("inline") or not record.parent_run_id:
        return False
    parent = get_run(record.parent_run_id)
    if parent is None or parent.source != "chat":
        return False
    siblings = [s for s in list_spawns(record.parent_run_id) if not s.metadata.get("inline")]
    return len(siblings) >= 2


def check_after_run(record: RunRecord) -> None:
    """Fire-and-forget batch check, called from runner._drive's finally block.

    Two triggers cover both finish orders: a spawn finishing may complete its
    batch (parent already done), and a chat turn finishing may leave behind a
    batch whose spawns all beat it to the finish line. Never raises — a
    synthesis problem must not break the run that triggered the check.
    """
    try:
        if record.source == "spawn" and record.parent_run_id:
            spawn_background(
                maybe_synthesise(record.parent_run_id),
                name=f"synthesis-check:{record.parent_run_id}",
            )
        elif record.source == "chat":
            spawn_background(
                maybe_synthesise(record.id),
                name=f"synthesis-check:{record.id}",
            )
    except Exception:
        log.exception("failed to schedule synthesis check for run %s", record.id)
