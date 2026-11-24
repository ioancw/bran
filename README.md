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

There is also a web app (a Svelte SPA served by `bran serve` at `/app/`, with a phone layout): chat, a Today page, the outputs of scheduled automations with a reading view, teammates, projects and settings. `USAGE.md` covers every surface in detail.

## Quick start

bran needs Python 3.10+ and a Claude login: either `ANTHROPIC_API_KEY` in `.env`, or a Claude
subscription signed in with `claude auth login` on the machine that runs it.

Linux, macOS or WSL:

```bash
python3 -m venv .venv-linux
.venv-linux/bin/pip install -e .
cp .env.example .env            # then set BRAN_API_TOKEN (and ANTHROPIC_API_KEY, if you use a key)

.venv-linux/bin/bran chat                                   # talk to the orchestrator
.venv-linux/bin/bran run research --task "Summarise today's news on Mars helicopter flights."
.venv-linux/bin/bran serve                                  # API, scheduler and web app at http://127.0.0.1:8765/app/
```

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env          # then edit it as above
bran chat
```

On Windows ARM the Claude CLI does not run natively: use WSL (see `USAGE.md`).

The web app's page is built once from `frontend/` (Node 18+): `cd frontend && npm ci && npm run build`.

Schedule a recurring run (the scheduler lives in the server process, so `bran serve` must be running):

```bash
bran schedule add daily-news research "Brief me on today's top AI news" --cron "0 8 * * *"
```

Call it over HTTP:

```bash
curl -H "Authorization: Bearer $BRAN_API_TOKEN" -H "Content-Type: application/json" \
     -d '{"task": "What is the capital of France?"}' http://127.0.0.1:8765/agents/research/run
```

## Agents

Every agent is one file in `agents/<name>.md`: frontmatter for the configuration (description, model, tools, `max_turns`, `subagents`), the body for the system prompt. The same file is the agent in both of its forms — the top-level agent you chat with, schedule or spawn, and the inline subagent another agent delegates to — so prompts and tools can't drift apart. MCP servers follow from tool names (`mcp__bran_docs__read_pdf` brings in the `bran_docs` server; `mcp__tavily__*` expands to every Tavily tool when `TAVILY_API_KEY` is set). Out of the box:

| Name | Role | Main tools |
| --- | --- | --- |
| `orchestrator` | The agent you chat with. Delegates to the others, fans out background runs, runs workflows, manages scheduled automations. | Agent, Workflow, spawn and runner tools, datasets and search |
| `research` | Web research with citations. | WebSearch, WebFetch, Tavily, read_pdf, datasets and search |
| `research-deep` | The same, on the strongest model, for high-stakes questions. | as `research` |
| `finance-news` | The morning finance briefing from a fixed set of feeds, saved and fact-checked. | fetch_url, Write, the fact-check workflow, search |
| `summariser` | Condenses documents and project activity. | Read, read_pdf, project tools |
| `filings-reader` | Reads one primary document end to end (minutes, a filing, a paper) and returns a note with page references; its numbers go into a dataset. | read_pdf, fetch_url, datasets |
| `quant-analyst` | Calculation over tables of market data (FX vol, rates) with SQL; shows the query behind each figure. | datasets |
| `verifier` | Re-derives the figures in a report from stored data and sources; lists what matches, differs or cannot be checked. | get_run_result, datasets and search |
| `watchlist-monitor` | Checks your watchlist against the news feeds; meant to run on a schedule with an alert condition. | fetch_url, datasets, the watchlist |

Each file's `tools:` list is the truth; the table is a summary.

Add a new agent by dropping a markdown file in `agents/` and restarting `bran serve`. List it under another agent's `subagents` to make it delegable there.

Every run is bounded: a per-run spend ceiling (`BRAN_RUN_BUDGET_USD`, default $5), a per-agent turn ceiling (`max_turns`), at most `BRAN_MAX_CONCURRENT_RUNS` background runs at once (the rest queue as pending), spawn nesting capped at `BRAN_MAX_SPAWN_DEPTH`, and the inline Agent-tool tree capped by `BRAN_SUBAGENT_DEPTH` / `BRAN_SUBAGENT_CONCURRENCY`. No agent ever sees Bash; only the tools a file grants exist in its session.

## Data: tables by handle, search over history

Agents do not paste tables to each other or do arithmetic in their heads. The `bran_data` tools
(an agent has them when its file lists `mcp__bran_data__*`) are:

| Tool | What it does |
| --- | --- |
| `write_dataset` | Store a table once (rows, or a CSV in the project folder). Returns a handle like `ds_a1f3c9` and a small manifest: typed columns, row count, first rows, source, caveats. |
| `dataset_head`, `list_datasets` | Look at a dataset by handle; find datasets earlier runs stored. |
| `query_dataset` | One SQL SELECT (DuckDB) over datasets named by handle. The result is a new dataset that records the SQL and the datasets it came from. |
| `render_dataset` | The table as markdown for a reply, or the whole of it as a CSV in the project's Files. |
| `search_briefings`, `search_runs` | Full-text search over the saved briefings (by section) and past run results, with dates and ids to cite. |

Rows are stored in bran's SQLite database. DuckDB is only the query engine: each query runs in a
throwaway in-memory database holding just the datasets its SQL names, with no file, network or
extension access, a row cap and a time limit. See `src/bran/datasets.py` and `docs/delegation.md`.

The same server keeps your **watchlist** (`get_watchlist`, `save_watchlist`): a few lines of
instruments with thresholds, themes and events. Tell the orchestrator what to watch and it saves
it; the `watchlist-monitor` agent reads it on each run.

`market_levels` is a dataset bran maintains itself: the levels the daily briefings state (a rate, a
price, a yield), each with the sentence it was read from. It is refreshed after a scheduled
briefing is delivered. It is headline-derived and sparse, and says so in its caveats.

```bash
bran index-briefings      # index the saved briefings for search (also done on first search)
bran levels               # read new briefings into market_levels now
```

## Skills

Skills live in `.claude/skills/<name>/SKILL.md` and are auto-loaded by the SDK. An example `web-research` skill ships with the project.

## Slash commands

`.claude/commands/*.md` files become slash commands in the REPL. An example `/digest` command is included.

## Layout

```
bran/
├── agents/              # one markdown file per agent (config + system prompt)
├── .claude/
│   ├── skills/          # auto-loaded skills
│   ├── commands/        # slash commands
│   └── workflows/       # saved workflows (e.g. fact-check)
├── src/bran/
│   ├── config.py        # env + paths
│   ├── agents.py        # agent registry (agent files -> ClaudeAgentOptions, MCP servers)
│   ├── runner.py        # the run loop around the SDK
│   ├── persistence.py   # SQLite store + numbered migrations
│   ├── scheduler.py     # APScheduler: automations, catch-up, heartbeat
│   ├── teammates.py     # standing roles with a brief, automations and a chat
│   ├── datasets.py      # tables by handle, SQL via a locked-down DuckDB
│   ├── search.py        # full-text search over runs and briefings
│   ├── market_levels.py # levels the briefings quote -> a dataset
│   ├── resume.py        # picks background runs up again after a restart
│   ├── notify.py        # push notifications and their rules
│   ├── tools/           # in-process MCP tools (spawn, runs, documents, data, team, ...)
│   ├── web/             # the SPA's API routes and built page
│   ├── cli.py           # Typer CLI
│   ├── api.py           # FastAPI app
│   └── repl.py          # interactive chat
├── frontend/            # the Svelte web app (built into src/bran/web/spa)
├── docs/delegation.md   # subagents vs spawned runs; passing data by handle
├── deploy/              # service unit
└── tests/
```

## License

MIT.
