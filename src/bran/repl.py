"""Interactive chat REPL with the orchestrator (or any agent).

Uses `ClaudeSDKClient` so the same session persists across user turns without
manual session ID juggling. Each user turn streams tokens/tool calls live.
"""

from __future__ import annotations

import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from bran.agents import build_options_for, get_agent

console = Console()


async def chat(agent_name: str = "orchestrator") -> None:
    agent = get_agent(agent_name)
    options = build_options_for(agent)

    console.print(
        Panel.fit(
            f"[bold cyan]bran[/] — chatting with [bold]{agent.name}[/]\n"
            f"[dim]{agent.description}[/]\n\n"
            "Commands: [yellow]/exit[/] to quit · [yellow]/clear[/] to restart session",
            border_style="cyan",
        )
    )

    async with ClaudeSDKClient(options=options) as client:
        session_id: str | None = None
        while True:
            try:
                user = Prompt.ask("\n[bold green]you[/]")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]bye.[/]")
                return

            user = user.strip()
            if not user:
                continue
            if user in {"/exit", "/quit"}:
                console.print("[dim]bye.[/]")
                return
            if user == "/clear":
                console.print("[dim]Starting a new session…[/]")
                await client.disconnect()
                await client.connect()
                continue

            await client.query(user)
            console.print(Rule(style="dim"))
            async for message in client.receive_response():
                _render(message, console)
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    session_id = (message.data or {}).get("session_id") or session_id
                if isinstance(message, ResultMessage):
                    cost = (
                        f"${message.total_cost_usd:.4f}"
                        if message.total_cost_usd is not None
                        else "n/a"
                    )
                    console.print(
                        f"[dim]done · {message.num_turns} turns · {cost} · "
                        f"session {session_id or '?'}[/]"
                    )


def _render(message, console: Console) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                console.print(Markdown(block.text))
            elif isinstance(block, ThinkingBlock):
                # Render thinking dimly so the user can see reasoning without
                # mistaking it for the final answer.
                console.print(f"[dim italic]thinking: {block.thinking[:200]}…[/]")
            elif isinstance(block, ToolUseBlock):
                console.print(f"[magenta]→ {block.name}[/] [dim]{_short(block.input)}[/]")


def _short(d: dict, limit: int = 120) -> str:
    s = str(d)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def run_chat(agent_name: str = "orchestrator") -> None:
    """Entry point used by the CLI."""
    asyncio.run(chat(agent_name))
