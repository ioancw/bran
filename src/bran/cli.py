"""Typer-based CLI: `bran <subcommand>`.

Subcommands:
  bran chat                 → interactive REPL with the orchestrator
  bran run <agent> --task ..→ one-shot run of any agent (cron-friendly)
  bran agents               → list registered agents
  bran runs list/show       → inspect past runs
  bran serve                → start the HTTP server + scheduler
  bran schedule add/list/rm → manage cron schedules
"""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from bran.config import SETTINGS
from bran.persistence import (
    RunRecord,
    ScheduleRecord,
    delete_schedule,
    get_run,
    insert_schedule,
    list_runs,
    list_schedules,
)
from bran.agents import list_agents
from bran.repl import run_chat
from bran.runner import run_agent_sync

app = typer.Typer(
    add_completion=False,
    help=(
        "bran — a fleet of Claude agents you can chat with, script, and schedule.\n\n"
        "Quickest path: `bran \"your question here\"` runs the orchestrator one-shot."
    ),
    no_args_is_help=True,
)
schedule_app = typer.Typer(help="Manage cron-style schedules.", no_args_is_help=True)
runs_app = typer.Typer(help="Inspect past agent runs.", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")
app.add_typer(runs_app, name="runs")

console = Console()


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------

@app.command()
def chat(
    agent: str = typer.Option(
        "orchestrator", "--agent", "-a", help="Which agent to chat with."
    ),
) -> None:
    """Open an interactive chat with the orchestrator (or another agent)."""
    run_chat(agent)


def _print_run_result(record: RunRecord, as_json: bool = False) -> None:
    """Shared output for one-shot run commands (`run`, `ask`)."""
    if as_json:
        from dataclasses import asdict

        typer.echo(json.dumps(asdict(record), indent=2, default=str))
        return
    if record.status == "completed":
        console.print(record.result or "[dim](no result text)[/]")
        console.print(
            f"\n[dim]run {record.id} · {record.num_turns} turns · "
            f"${record.total_cost_usd or 0:.4f} · session {record.session_id}[/]"
        )
    else:
        console.print(f"[red]Run {record.status}.[/] {record.error or ''}")
        raise typer.Exit(code=1)


@app.command()
def run(
    agent: str = typer.Argument(..., help="Agent name (e.g. research, summariser)."),
    task: str = typer.Option(..., "--task", "-t", help="The task/prompt to run."),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Resume a previous session by its session_id."
    ),
    max_turns: Optional[int] = typer.Option(
        None, "--max-turns", help="Cap agentic turns."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print the full run record as JSON instead of just the result."
    ),
) -> None:
    """One-shot run of any agent. Returns when done."""
    record = run_agent_sync(
        agent, task, resume_session=resume, max_turns=max_turns
    )
    _print_run_result(record, as_json=as_json)


@app.command()
def ask(
    agent: str = typer.Argument(..., help="Agent name."),
    task: list[str] = typer.Argument(
        ..., help="The prompt. Wrap in quotes or let the shell split words."
    ),
    resume: Optional[str] = typer.Option(None, "--resume"),
    max_turns: Optional[int] = typer.Option(None, "--max-turns"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Ergonomic shortcut for `run`: `bran ask research "what is MCP?"`.

    All non-option positional words after the agent name are joined into the
    task string, so you can omit --task and even drop the quotes if your prompt
    has no shell-special characters.
    """
    record = run_agent_sync(
        agent, " ".join(task), resume_session=resume, max_turns=max_turns
    )
    _print_run_result(record, as_json=as_json)


@app.command()
def agents() -> None:
    """List registered agents."""
    table = Table(title="Agents", show_lines=False)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Model")
    table.add_column("Tools")
    table.add_column("Description")
    for p in list_agents():
        table.add_row(
            p.name,
            p.model or "(default)",
            ", ".join(p.tools[:6]) + ("…" if len(p.tools) > 6 else ""),
            p.description,
        )
    console.print(table)


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, "--host", help="Override BRAN_HOST."),
    port: Optional[int] = typer.Option(None, "--port", help="Override BRAN_PORT."),
    no_scheduler: bool = typer.Option(
        False, "--no-scheduler", help="Run the HTTP API without the cron scheduler."
    ),
) -> None:
    """Start the FastAPI HTTP server (and APScheduler unless --no-scheduler)."""
    import uvicorn
    from bran.api import build_app

    if SETTINGS.api_token is None:
        console.print(
            "[red]Refusing to start: BRAN_API_TOKEN is unset.[/] "
            "Set it in your .env or environment first."
        )
        raise typer.Exit(code=2)

    app_obj = build_app(enable_scheduler=not no_scheduler)
    h, p = host or SETTINGS.host, port or SETTINGS.port
    console.print(
        f"[green]bran serve[/] · http://{h}:{p} · "
        f"scheduler={'on' if not no_scheduler else 'off'}"
    )
    uvicorn.run(app_obj, host=h, port=p, log_level="info")


# ---------------------------------------------------------------------------
# bran runs
# ---------------------------------------------------------------------------

@runs_app.command("list")
def runs_list(
    agent: Optional[str] = typer.Option(None, "--agent", "-a"),
    status: Optional[str] = typer.Option(None, "--status", "-s"),
    limit: int = typer.Option(25, "--limit", "-n"),
) -> None:
    """List recent runs."""
    rows = list_runs(agent=agent, status=status, limit=limit)
    if not rows:
        console.print("[dim]No runs yet.[/]")
        return
    table = Table()
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Agent", style="cyan")
    table.add_column("Status")
    table.add_column("Started")
    table.add_column("Cost")
    table.add_column("Task", overflow="fold")
    for r in rows:
        cost = f"${r.total_cost_usd:.4f}" if r.total_cost_usd is not None else "-"
        status_colour = {
            "completed": "green",
            "failed": "red",
            "running": "yellow",
            "pending": "dim",
        }.get(r.status, "white")
        table.add_row(
            r.id[:8],
            r.agent,
            f"[{status_colour}]{r.status}[/]",
            r.started_at.replace("T", " ")[:19],
            cost,
            r.task[:60] + ("…" if len(r.task) > 60 else ""),
        )
    console.print(table)


def _resolve_run(run_id: str) -> RunRecord:
    """Look up a run by exact id or unique prefix. Exits on miss/ambiguity."""
    record = get_run(run_id)
    if record is not None:
        return record
    candidates = [r for r in list_runs(limit=500) if r.id.startswith(run_id)]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        console.print(
            f"[yellow]Ambiguous prefix '{run_id}'. Matches: "
            + ", ".join(c.id[:12] for c in candidates)
        )
        raise typer.Exit(code=1)
    console.print(f"[red]No run with id {run_id}[/]")
    raise typer.Exit(code=1)


@runs_app.command("show")
def runs_show(run_id: str = typer.Argument(...)) -> None:
    """Show full detail for a run (accepts id prefix)."""
    from dataclasses import asdict

    record = _resolve_run(run_id)
    typer.echo(json.dumps(asdict(record), indent=2, default=str))


@runs_app.command("watch")
def runs_watch(
    run_id: str = typer.Argument(..., help="Run id or unique prefix."),
    interval: float = typer.Option(2.0, "--interval", "-i", help="Poll seconds."),
    timeout: float = typer.Option(
        0.0, "--timeout", help="Give up after N seconds (0 = wait forever)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the final record as JSON."),
) -> None:
    """Block until a background run finishes, then print its result.

    Useful after the orchestrator (or HTTP API) spawned a background run and
    you want to wait for it without polling `bran runs show` by hand.
    """
    import time

    from rich.status import Status

    record = _resolve_run(run_id)
    if record.status in {"completed", "failed"}:
        _print_run_result(record, as_json=as_json)
        return

    start = time.monotonic()
    with Status(
        f"watching {record.id[:8]} ({record.agent})…", console=console, spinner="dots"
    ) as status:
        while record.status in {"pending", "running"}:
            elapsed = int(time.monotonic() - start)
            if timeout and elapsed >= timeout:
                console.print(
                    f"[yellow]Timed out after {timeout:.0f}s — run still {record.status}.[/]"
                )
                raise typer.Exit(code=2)
            time.sleep(interval)
            refreshed = get_run(record.id)
            if refreshed is None:
                console.print("[red]Run vanished from DB.[/]")
                raise typer.Exit(code=1)
            record = refreshed
            status.update(
                f"watching {record.id[:8]} ({record.agent}) · "
                f"{record.status} · {int(time.monotonic() - start)}s elapsed"
            )

    _print_run_result(record, as_json=as_json)


# ---------------------------------------------------------------------------
# bran schedule
# ---------------------------------------------------------------------------

@schedule_app.command("add")
def schedule_add(
    name: str = typer.Argument(..., help="Unique name for the schedule."),
    agent: str = typer.Argument(..., help="Agent to run."),
    task: str = typer.Argument(..., help="Task/prompt to send each tick."),
    cron: str = typer.Option(
        ..., "--cron", help="5-field cron expression, e.g. '0 8 * * *' for 08:00 daily."
    ),
) -> None:
    """Register a recurring schedule. Active once `bran serve` is running."""
    record = ScheduleRecord.new(name=name, agent=agent, task=task, cron=cron)
    insert_schedule(record)
    console.print(
        f"[green]Added schedule[/] [cyan]{name}[/] → {agent} · cron='{cron}'\n"
        f"[dim]Run `bran serve` (or restart it) to activate.[/]"
    )


@schedule_app.command("list")
def schedule_list() -> None:
    """List all schedules."""
    rows = list_schedules()
    if not rows:
        console.print("[dim]No schedules configured.[/]")
        return
    table = Table()
    table.add_column("Name", style="cyan")
    table.add_column("Agent")
    table.add_column("Cron")
    table.add_column("Enabled")
    table.add_column("Task", overflow="fold")
    for s in rows:
        table.add_row(
            s.name, s.agent, s.cron,
            "yes" if s.enabled else "no",
            s.task[:80] + ("…" if len(s.task) > 80 else ""),
        )
    console.print(table)


@schedule_app.command("rm")
def schedule_rm(name: str = typer.Argument(...)) -> None:
    """Remove a schedule by name."""
    if delete_schedule(name):
        console.print(f"[green]Removed[/] {name}")
    else:
        console.print(f"[red]No schedule named {name}[/]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Entry point — supports the bare-prompt shortcut `bran "your question"`.
# ---------------------------------------------------------------------------

# Subcommand names registered above. Used by `main()` to distinguish a real
# subcommand from a free-form prompt the user wants the orchestrator to handle.
# Update this set if you add a new top-level command.
_SUBCOMMANDS = {"chat", "run", "ask", "agents", "serve", "schedule", "runs"}


def main() -> None:
    """CLI entry point.

    Special case: when the first positional word isn't a known subcommand
    (and the user didn't pass --help), rewrite argv as
    `bran ask orchestrator <prompt...>` so the existing `ask` machinery
    handles flags like --json, --max-turns, --resume uniformly.

    Examples:
        bran "ask about mcps"             → bran ask orchestrator "ask about mcps"
        bran what is mcp                  → bran ask orchestrator what is mcp
        bran chat                         → unchanged (subcommand)
        bran ask research "foo" --json    → unchanged (subcommand)
    """
    import sys

    args = sys.argv[1:]
    first_non_flag = next((a for a in args if not a.startswith("-")), None)
    is_help = any(a in {"-h", "--help"} for a in args)

    if first_non_flag is not None and first_non_flag not in _SUBCOMMANDS and not is_help:
        idx = args.index(first_non_flag)
        sys.argv = [sys.argv[0], *args[:idx], "ask", "orchestrator", *args[idx:]]

    app()


if __name__ == "__main__":
    main()
