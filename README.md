# bran

A fleet-orchestration agent platform built on the **Claude Agent SDK** — think *Claude Code, but general-purpose*. One orchestrator agent you can chat with, a roster of specialised sub-agents (each with their own system prompt, tools, skills, and permissions), and several surfaces to drive them: an interactive REPL, a one-shot CLI, a Python library API, a web UI, and an authenticated HTTP server with cron-style scheduling.

## What's in the box

| Surface | How you use it |
| --- | --- |
| `bran chat` | Drop into an interactive conversation with the orchestrator. It can delegate to sub-agents and use skills. |
| `bran run <agent> --task "..."` | Run any agent one-shot. Returns when done, prints structured output. Cron-friendly. |
| `from bran import run_agent` | Fire an agent run from any Python script. |
| `bran serve` | FastAPI server (localhost + bearer-token auth) exposing `/agents/{name}/run`, `/runs`, `/schedules`. |
| `bran schedule add ...` | APScheduler cron triggers that fire agent runs on a recurring schedule. |

All runs are persisted to SQLite (under `./.bran/` by default), with Agent SDK session IDs captured so you can resume conversations.

## Quick start

```powershell
# 1. Install (creates a venv and installs the package in editable mode)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# 2. Configure
copy .env.example .env
# then edit .env and set ANTHROPIC_API_KEY + BRAN_API_TOKEN

# 3. Talk to the orchestrator
bran chat

# 4. Run a single agent one-shot
bran run research --task "Summarise the latest news on Mars helicopter flights."

# 5. Start the HTTP server
bran serve
# then in another shell:
curl -H "Authorization: Bearer $env:BRAN_API_TOKEN" `
     -H "Content-Type: application/json" `
     -d '{"task": "What is the capital of France?"}' `
     http://127.0.0.1:8765/agents/research/run

# 6. Schedule a recurring run
bran schedule add daily-news research "Brief me on today's top AI news" --cron "0 8 * * *"
bran serve  # the scheduler runs inside the server process
```

## Agents

Agents are defined in `src/bran/agents.py` as `Agent` objects, plus an optional filesystem definition in `.claude/agents/<name>.md`. Out of the box:

| Name | Role | Tools |
| --- | --- | --- |
| `orchestrator` | Default conversational agent. Delegates to others and can spawn background runs. | Read, Glob, Grep, WebSearch, WebFetch, Agent, spawn_agent |
| `research` | Web research + summarisation. | WebSearch, WebFetch, Read, Write |

Add a new agent by editing `agents.py` or dropping a markdown file in `.claude/agents/`.

## Skills

Skills live in `.claude/skills/<name>/SKILL.md` and are auto-loaded by the SDK. An example `web-research` skill ships with the project.

## Slash commands

`.claude/commands/*.md` files become slash commands in the REPL. An example `/digest` command is included.

## Layout

```
bran/
├── .claude/
│   ├── agents/        # filesystem-defined subagents
│   ├── skills/        # auto-loaded skills
│   └── commands/      # slash commands
├── src/bran/
│   ├── config.py      # env + paths
│   ├── agents.py      # Agent registry (translates to ClaudeAgentOptions)
│   ├── persistence.py # SQLite store for runs + schedules
│   ├── runner.py      # core run loop wrapping the SDK
│   ├── repl.py        # interactive chat (ClaudeSDKClient)
│   ├── tools/         # in-process MCP tools
│   ├── cli.py         # Typer CLI
│   ├── api.py         # FastAPI server
│   └── scheduler.py   # APScheduler integration
└── scripts/
    └── example_programmatic.py
```

## License

MIT.
