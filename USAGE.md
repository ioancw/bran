# Using bran

How to actually invoke bran day-to-day. For setup and architecture, see [`README.md`](README.md).

## At a glance

| You want to… | Command |
| --- | --- |
| Quick one-shot question, no ceremony | `bran "your question here"` |
| Ask a specific agent one-shot | `bran ask <agent> "your question"` |
| One-shot with flags / scripting | `bran run <agent> --task "..." [--json] [--max-turns N] [--resume <id>]` |
| Interactive chat with the orchestrator | `bran chat` |
| Chat with a specific agent | `bran chat --agent research` |
| List registered agents | `bran agents` |
| See recent runs | `bran runs list [--agent NAME] [--status STATE] [--limit N]` |
| Show one run in full | `bran runs show <id-prefix>` |
| Wait for a background run to finish | `bran runs watch <id-prefix>` |
| Start the HTTP server + scheduler | `bran serve` |
| Add a cron schedule | `bran schedule add <name> <agent> "<task>" --cron "0 8 * * *"` |
| List / remove schedules | `bran schedule list` · `bran schedule rm <name>` |

## The bare-prompt shortcut

```bash
bran "summarise today's AI news"
bran what is mcp                              # words without quotes work too
bran "..." --json --max-turns 5               # flags pass through
```

Mechanism: when the first positional word isn't a known subcommand, the entry point rewrites `sys.argv` to `bran ask orchestrator <your-prompt>`. So everything that works for `ask` (`--json`, `--max-turns`, `--resume`) works here too. `bran` with no args or `--help` shows help instead.

## `ask` vs `run`

Same underlying call. Cosmetic difference:

```bash
bran run research --task "what is MCP?"      # explicit, scriptable
bran ask research "what is MCP?"              # quote-light, interactive
```

Use `run` in shell scripts where you want the `--task` flag for clarity. Use `ask` (or bare-prompt) when typing by hand.

## Agents and delegation

The orchestrator is the default chat agent. It can hand work off to specialists in two distinct ways:

| | **Agent tool** (SDK subagents) | **`spawn_agent`** (bran's custom MCP tool) |
| --- | --- | --- |
| Sync vs async | Synchronous — orchestrator waits | Asynchronous — returns immediately |
| Returned to orchestrator | The subagent's full output | Just a `run_id`, nothing else |
| Use when | "Answer this *using* research" — you want the result *in this conversation* | "Kick off X *in the background*" — fire and forget |
| Persistence | One row (orchestrator's run) | Separate run row in bran's SQLite |

Trigger them by phrasing:

```bash
bran chat
# → triggers the Agent tool (sync):
>>> use the research agent to find three news items about MCP this week
# → triggers spawn_agent (async):
>>> spawn a background research run on this week's MCP news, just give me the run id
```

In the REPL stream you'll see `→ Agent {...}` or `→ spawn_agent {...}` lines confirming which path was taken. In one-shot mode (`bran "..."`) you only see the final text plus the footer; delegation is implied by higher turn counts (4–8) and inline `(Source: domain)` citations.

To explicitly invoke a specific subagent regardless of phrasing, just name it: *"Use the `research` agent to…"* bypasses the orchestrator's discretion.

## Auth: API key vs subscription

Two paths, mutually exclusive:

- **Subscription** (default if you've run `claude login`): no `ANTHROPIC_API_KEY` in `.env`. bran spawns the system `claude` CLI which uses whatever auth `claude` itself is using. Cost numbers in the footer are estimates only; you're not actually charged.
- **API key**: set `ANTHROPIC_API_KEY` in `.env` (or env var). Takes precedence over subscription auth. Real billing applies.

**Important date:** from June 15, 2026, Agent SDK usage on subscription plans draws from a separate monthly "Agent SDK credit" rather than your interactive Claude Code quota. So bran runs don't eat into your interactive sessions, but they're capped by that bucket.

## Background runs and process lifetime

`spawn_agent` (from the orchestrator) and HTTP `POST /agents/.../run` with `background: true` both run the work as an `asyncio.Task` on the **current process's event loop**. That means:

| Spawned from | Survives? |
| --- | --- |
| `bran chat` REPL | Only until `/exit` — task dies with the REPL |
| `bran run` one-shot | Process ends when the one-shot finishes; background tasks die |
| `bran serve` (HTTP or scheduled) | Yes — server keeps the loop alive |

For durable async work: spawn via the HTTP API while `bran serve` is up, or use a schedule, or run a long-lived process. To wait for one without polling by hand:

```bash
bran runs watch <run-id>         # spinner until status flips to completed/failed
bran runs watch <id> --interval 1 --timeout 600
```

## Notifications

Two built-in notifiers, both opt-in via env vars (in `.env` or environment):

```bash
BRAN_NOTIFY_BELL=1                                   # beep + one-line summary to stderr
BRAN_NOTIFY_WEBHOOK_URL=https://ntfy.sh/your-topic   # POST run JSON to a webhook
```

The webhook target works with [ntfy.sh](https://ntfy.sh) (push to phone), Slack incoming webhooks, Discord webhooks, or any URL that accepts JSON.

Custom notifiers from Python:

```python
from bran import register_notifier

def my_notifier(record):
    print(f"{record.agent} {record.status}: {record.result}")

register_notifier(my_notifier)
```

Hooks fire in a `finally:` block in the runner, so a notifier crash can't break the run.

## Web UI + HTTP API

`bran serve` exposes two surfaces on the same port:

**Web UI** (no auth in v1 — the `get_current_user()` stub is the seam for OAuth later):

| Path | Purpose |
| --- | --- |
| `/` | Dashboard — fire a new run, see recent runs |
| `/runs`, `/runs/{id}` | Browse runs (filterable) and inspect one in detail |
| `/agents` | Agent roster |
| `/schedules` | Add/remove cron schedules |

The UI is HTMX + Tailwind, served from the same FastAPI process. Run rows live-refresh while a run is in flight (3s polling). Just point a browser at `http://127.0.0.1:8765`.

**JSON API** (bearer auth required, prefixed `/api`):

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | No auth — health probe |
| `GET` | `/api/agents` | List agents |
| `POST` | `/api/agents/{name}/run` | Run an agent (set `"background": true` to fire-and-forget) |
| `GET` | `/api/runs` | Filter recent runs (`?agent=&status=&limit=`) |
| `GET` | `/api/runs/{id}` | Inspect a single run |
| `GET` | `/api/schedules` | List schedules |
| `POST` | `/api/schedules` | Create a schedule |
| `DELETE` | `/api/schedules/{name}` | Remove a schedule |

Server binds to `127.0.0.1:8765` by default; change with `BRAN_HOST` / `BRAN_PORT`. Refuses to start without `BRAN_API_TOKEN` configured.

For small-team use, issue **named tokens** instead of sharing one secret: `BRAN_API_TOKENS="ioan:tok-abc,partner:tok-def"`. Each token authenticates the same API, and runs it triggers are attributed to its name (`runs.actor`, shown on the run detail page). The single `BRAN_API_TOKEN` still works and is attributed as `api`.

Example API call:

```bash
curl -H "Authorization: Bearer $BRAN_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"task": "what is MCP?", "background": true}' \
     http://127.0.0.1:8765/api/agents/research/run
# → {"run_id": "...", "status": "running", "background": true}
```

## Scheduling

Three realistic options, ranked by what to actually pick:

### 1. bran's built-in scheduler (the path of least resistance)

```bash
bran schedule add morning-digest research "Brief me on today's top AI news" --cron "0 8 * * *"
bran serve   # in tmux or screen so it survives terminal close
```

APScheduler inside the same `bran serve` process. Only fires while `bran serve` is up; on a laptop that means while the host is on and your terminal session is alive. Schedules persist in SQLite so they survive restarts.

**Reliability:** a failed scheduled run is retried automatically with backoff (1m → 5m → 15m, up to `BRAN_RUNNER_RETRIES` times, default 2) — a flaky feed doesn't cost you the day's briefing. Every run is also bounded by `BRAN_RUN_TIMEOUT` (seconds, default 3600, 0 = off) so a hung SDK subprocess can't wedge the scheduler.

**Output quality modes** (per runner — set in the UI form, the runner's edit page, or in chat via create_runner):

- **verify** — after each run, a cheap evaluator reviews the output against the task (the cookbook evaluator-optimizer pattern). A failed verdict re-runs the agent ONCE with the reviewer's feedback; both verdicts are stored on the runs and shown on the run detail page. Verification fails open: a broken critic never blocks delivery.
- **delta** — each fire sees the previous completed report and is told to report only what's NEW or CHANGED (dedupe, lead with deltas, say "nothing changed" when true). Turns a recurring news/research runner from a state dump into a signal feed.

### 2. systemd timers (if you want OS-level scheduling on Linux/WSL)

Modern Linux replacement for cron. Better logs (`journalctl -u bran-digest.timer`). Useful if you already maintain other systemd units, otherwise it duplicates bran's scheduler.

```ini
# /etc/systemd/system/bran-digest.service
[Service]
Type=oneshot
User=iwilliams
WorkingDirectory=/mnt/c/Users/ioanc/github/bran
ExecStart=/mnt/c/Users/ioanc/github/bran/.venv-linux/bin/bran ask research "today's AI news"

# /etc/systemd/system/bran-digest.timer
[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

### 3. Move bran off your laptop (the only real fix)

If you actually want runs at 8am every day, put bran on something that's always on:

- Cheap VPS (Hetzner CX11 ≈ €4/mo): Ubuntu, `pip install -e .`, `bran serve` under systemd
- Raspberry Pi or NAS at home
- Existing always-on home server / container host

Same code, same scheduler — just somewhere that's not your laptop. The HTTP API becomes reachable from anywhere with your bearer token; pair with `BRAN_NOTIFY_WEBHOOK_URL` for push notifications.

**Avoid:** Windows Task Scheduler (rubbish), and plain cron unless you already like it (systemd timers are strictly better).

## Library use (Python)

```python
from bran import run_agent_sync, run_agent, register_notifier

# Synchronous, for scripts and notebooks
record = run_agent_sync("research", "what is MCP?")
print(record.result)

# Async, for integration with other async code
import asyncio
record = asyncio.run(run_agent("research", "what is MCP?"))
```

Both return a `RunRecord` with `.id`, `.session_id`, `.status`, `.result`, `.total_cost_usd`, `.num_turns`, `.duration_ms`.

To resume a previous conversation, pass the `session_id`:

```python
record2 = run_agent_sync("research", "Now go deeper on point 2", resume_session=record.session_id)
```

## Platform note: Windows ARM

The bundled `claude.exe` has a [known `STATUS_ACCESS_VIOLATION` crash](https://github.com/anthropics/claude-code/issues/51898) on Windows ARM64 that takes down every SDK call before the prompt is sent. There's no fix as of May 2026 and the related [SDK issue is closed as not-planned](https://github.com/anthropics/claude-agent-sdk-python/issues/208).

**Workaround:** run bran from inside WSL Ubuntu. Same project checkout, separate `.venv-linux/` venv, identical commands. Windows x64 is unaffected (the bug is ARM-specific).
