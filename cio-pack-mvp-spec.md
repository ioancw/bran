# CIO Monthly PnL Pack: MVP Build Specification (v3, two agents, agent runtime)

Audience: the engineering agent building this system inside our own infrastructure.
Status: design agreed at MVP level. Items marked **[CONFIRM]** need an answer from the owner before you build that part. Do not guess them.

Changes: v2 added a second agent (an independent verifier). v3 adds section 7, the agent runtime, optimised for the Claude Agent SDK.

---

## 1. Purpose

Produce a monthly fixed income PnL and performance pack for the Office of the CIO, risk and the valuations team. The pack is for a pod-based multi-manager hedge fund. The audience is internal and is not the pods.

The value is not in retrieving data, since everyone has the same data. The value is in cross-pod synthesis, independent checks, consistent definitions and fast, auditable explanation. The MVP proves the pipeline end to end. It is not yet the full pack.

## 2. Core principles (non-negotiable)

1. **Code does the numbers. Models never do arithmetic, and never retype a table.** Every figure comes from SQL or Python over stored data.
2. **Pass data by handle, not by value.** Each stage writes a dataset to the store and passes on a handle plus a manifest. No tables are pasted into prompts or between agents.
3. **The orchestrator is a deterministic script** (`run_pack.py`), not an LLM. The order of stages is fixed. Agents are leaves: no agent calls another agent.
4. **The model writes words only**, as schema-validated JSON placed into slots in a fixed template. It never writes HTML structure.
5. **Fail closed.** A failed check stops the run with a clear message. Do not ship a pack with a footnote.
6. **Everything is reproducible.** Same data and same code give identical numbers and structure. Only prose may vary slightly.
7. **The APIs are the source of truth.** The local cache is a copy that can be deleted and rebuilt.
8. **Agents have narrow, read-only access.** Only loaders and section code write to the store.
9. **The verifier agent is independent.** It never sees the explainer's prompt, reasoning or intermediate output, only the final commentary and the data.

## 3. MVP scope

**In scope**
- One closed month per run, plus a few days either side for context.
- Two data sources: **daily PnL** and **attribution**. Both are standardised across pods and available via API for date ranges and YTD.
- Three sections: **S1 firm summary**, **S2 attribution by pod**, **S4 light scorecard**.
- **Two agents:** the **explainer** (one call per section) and the **verifier agent** (one call per section).
- A **code-based verifier** for figures, which runs before the verifier agent.
- One HTML output and one `figures.json` sidecar.
- One command: `python run_pack.py 2026-10`.

**Out of scope for the MVP** (design so they can be added later)
- Risk and trade data, therefore S3 expected-vs-actual, S5 cross-pod and S6 top and bottom trades.
- Intraday PnL, monitoring and alerts, scheduling.
- Parallel workflow orchestration beyond simple per-section parallelism, a memory layer, the chat Q&A agent, PDF output.
- Charts (add only if trivial).

## 4. Architecture

```
PnL API ──┐
          ├─► loaders ─► cache (parquet, by date) ─► DuckDB views
Attr API ─┘                                              │
                                         checks (stop on fail)
                                                         │
                          section queries S1, S2, S4 ─► runs/<id>/*.parquet + manifests
                                                         │
                         AGENT 1: explainer ─► commentary JSON (schema-validated)
                                                         │
                         code verifier (numbers match?) ── fail ─► stop
                                                         │
                         AGENT 2: verifier ─► verdict JSON per claim
                                                         │
                  all supported ─► render │ unsupported ─► one retry to explainer ─► re-verify
                                                         │
                       renderer (Jinja2) ─► pack.html + figures.json
```

`run_pack.py` is the orchestrator. It is run by a person (or later a scheduler) from a terminal. It calls each agent as a function, once per section.

### Technology
- Python 3.11 or later.
- **DuckDB over parquet** is the store. No database server.
- `pandas` or `polars`, `pyarrow`, `jinja2`, `pyyaml`, `jsonschema` (or `pydantic`), `pytest`.
- Agent runtime: see section 7.

### Repository layout

```
cio-pack/
  config/
    definitions.yaml     # PnL, buckets, sign conventions, pods, calendar
    tolerances.yaml      # reconciliation and rounding tolerances
  loaders/               # pnl.py, attribution.py
  checks/                # coverage.py, reconcile.py
  sections/              # s1_summary.py, s2_attribution.py, s4_scorecard.py
  agents/
    explainer_prompt.md
    verifier_prompt.md
    runtime.py           # run_explainer(), run_verifier(): the only code that calls the model
    tools/query_tool.py  # the single read-only SQL tool
    schemas/             # s1.json, s2.json, s4.json (commentary), verdict.json
  skills/
    pnl-definitions.md
    cio-report-style.md
  templates/             # base.html.j2, s1.html.j2, s2.html.j2, s4.html.j2, style.css
  verify/                # figures.py (code verifier)
  run_pack.py            # the orchestrator
  store/                 # NOT in git: cache/, runs/
  tests/
```

### Store layout

```
store/
  cache/
    pnl/date=YYYY-MM-DD/part.parquet
    attribution/date=YYYY-MM-DD/part.parquet
  runs/
    pack_YYYY_MM/
      manifest.json                # run ID, as-of times, code and template versions
      s1_summary.parquet (+ .manifest.json)
      s2_attribution.parquet
      s4_scorecard.parquet
      commentary/s1.json ...
      verdicts/s1.json ...
      audit/                       # every prompt, tool call, response
      pack.html
      figures.json
```

`runs/` folders are immutable once a run finishes. Keep every one, including commentary, verdicts and the audit log.

## 5. Data contracts

Define each table precisely in `config/definitions.yaml` before writing loaders. For every table, record the items below. Fill the columns from the actual API response; do not invent them.

| Item | PnL | Attribution |
|---|---|---|
| Grain | one row per pod and date **[CONFIRM: also by desk or strategy?]** | one row per pod, date and bucket **[CONFIRM]** |
| Partition | date | date |
| Columns, types, units | **[CONFIRM from API]** | **[CONFIRM from API]** |
| Currency and sign convention | **[CONFIRM]** | **[CONFIRM]** |
| Bucket names | n/a | **[CONFIRM: carry, curve, spread, vol, FX, residual, or other]** |
| Reconciliation rule | sum equals the official firm PnL by pod and date | sum of buckets equals PnL by pod and date |

Every loaded partition must also store a manifest: source API and version, request parameters, load timestamp, row count and a content hash.

**Restatements.** Past PnL can change. On every refresh, re-pull a trailing window **[CONFIRM: N days, suggest 10 business days]** and replace those partitions. Run a full-month reconciliation before each pack.

## 6. Stages and requirements

### Stage 1: Load
- One loader per API. Pull a date range, validate types and non-nulls, write partitioned parquet and its manifest.
- Return a **summary only** (row count, date range, pods present, gaps). Never return rows into model context.

### Stage 2: Checks (gate)
Run all of these. Any failure stops the run and prints the failing pod, date and difference.
1. **Coverage:** every expected pod and every business day present. Use the calendar in `definitions.yaml` **[CONFIRM: holiday calendar]**.
2. **Reconcile to source:** cache PnL equals the official figure by pod and date, within tolerance.
3. **Attribution ties:** buckets sum to PnL by pod and date, within tolerance.
4. **Pods sum to firm** for each date.

Tolerances live in `config/tolerances.yaml`. Do not hard-code them.

### Stage 3: Sections
Each section is a function that takes a month and returns a dataset plus a manifest. Use SQL over DuckDB. Each manifest records the query text, the data as-of time, and any caveats.

- **S1 Firm summary:** month PnL, YTD PnL, hit rate, annualised volatility, maximum drawdown for the firm, and PnL by pod.
- **S2 Attribution by pod:** month PnL by pod and bucket, with firm totals. **Invariant: S2 totals equal S1 totals.** Check this in code.
- **S4 Scorecard (light):** per pod, month PnL, YTD PnL, volatility, hit rate, maximum drawdown. Metric formulas are defined **once** in `definitions.yaml` **[CONFIRM: annualisation factor, drawdown basis, hit rate definition]**.

### Stage 4: Agent 1, the explainer
- One call per section. Input: the section handle, its manifest, and a **small preview** (top rows or summary figures). The agent may run read-only, capped queries through the query tool for further detail.
- Output: **JSON only**, validated against `agents/schemas/sN.json`. Retry once on validation failure, then stop the run.
- Required fields (per section, adapt the schema): `headline` (max 140 characters), `claims` (list of short statements, max 5, each with an id), `watch_items` (list), `confidence` (`high`, `medium` or `low`), and `no_clear_driver` (boolean).
- Write commentary as discrete, individually checkable claims so the verifier can assign a verdict to each.
- The prompt must instruct: state only what the data supports; use `no_clear_driver: true` instead of inventing a story; never produce a number that is not in the section data; do not assert causes (events, news, positioning) that are not in the data.
- Load the two skills (section 8) into context.

### Stage 5: Code verifier (gate)
- Extract every number in each commentary JSON and check it against the section dataset, with a rounding tolerance.
- Check that quoted pod and bucket names exist.
- Any unmatched or unverifiable figure fails the run, listing the offending sentence.
- Verify against the **cache or section data**, not against the explainer's own output.
- This runs before the verifier agent because it is cheap and catches transcription errors.

### Stage 6: Agent 2, the verifier
Purpose: judge what code cannot, namely whether each claim is actually supported by the data.

**Independence rules**
- It receives only: the commentary JSON, the section handle and manifest, and read-only query access to the **cache and section data**.
- It must **not** receive the explainer's prompt, skills, reasoning or any earlier drafts.
- Its own prompt is `agents/verifier_prompt.md`.

**What it checks**
1. **Support:** each claim is true against the data. It re-derives the claim with its own queries.
2. **Causal overreach:** flag any claim asserting a cause or event that is not in the data.
3. **Omission:** the largest contributors and detractors for the section are covered. Flag material omissions.
4. **Calibration:** words such as "sharply", "significant" and "driven by" match the actual size, using thresholds in the style skill.
5. **Cross-section consistency:** figures and statements agree across S1, S2 and S4.

**Output:** JSON validated against `agents/schemas/verdict.json`, one entry per claim:

```
{ "claim_id": "...", "verdict": "supported | unsupported | cannot_check",
  "evidence_query": "...", "reason": "one line" }
```

plus a section-level `omissions` list.

**Handling verdicts**
- All `supported`: proceed to render.
- Any `unsupported` or `cannot_check`: send the explainer the objections (claim id, reason only) for **one** retry, then run the code verifier and verifier agent again.
- Still failing after the retry: stop the run, or, if the owner enables it **[CONFIRM]**, render with the failing claims removed and a visible flag.
- Store every verdict file in the run folder.

### Stage 7: Render
- Jinja2, one base template and one partial per section.
- **Tables and (optional) charts are rendered by code directly from the stored datasets.** Apply number formatting rules from a single function: decimals, thousands separators, sign and colour, units.
- Commentary is inserted only into defined slots.
- Footer: template version, code version, data as-of times, number of claims verified, and the retry count.
- Output a single self-contained HTML file (embedded CSS) and `figures.json` mapping every figure in the pack to its dataset handle and query.

## 7. Agent runtime (Claude Agent SDK)

This section says how the two agents are run. Verify SDK option names against the current official documentation before building; do not rely on this document for exact API signatures.

**Choice of runtime [CONFIRM].** Preferred: the **Claude Agent SDK** (Python), called from `agents/runtime.py`. Acceptable alternative: the Claude API directly with tool use, if the owner prefers fewer dependencies. Both agents and the single tool are simple enough for either. The model endpoint (Anthropic API, or a cloud provider's hosted service) must satisfy the data policy in section 13 **[CONFIRM]**.

**Orchestration stays in `run_pack.py`.**
- The script makes one SDK call per agent per section. It does not use a lead agent that delegates to the two as subagents, because that would make the stage order depend on model decisions.
- `agents/runtime.py` exposes exactly two functions, `run_explainer()` and `run_verifier()`. They are the only code that talks to the model.
- Sections may run in parallel. Each call is independent and uses its own fresh session.

**Per-agent configuration**

| | Explainer | Verifier |
|---|---|---|
| System prompt | `explainer_prompt.md` | `verifier_prompt.md` |
| Skills available | `pnl-definitions`, `cio-report-style` | `pnl-definitions` only |
| Tools | `query_tool` only | `query_tool` only |
| Session | fresh per section | fresh per section, **never shares context with the explainer** |
| Output | commentary JSON | verdict JSON |

**Tools and permissions**
- Expose **one custom tool**, `query_tool`, as an in-process tool or a small MCP server. It runs read-only SQL against DuckDB, enforces a row cap and a query timeout, and logs every call.
- **Disable every built-in tool**: no file read or write, no shell, no web. Use an explicit allowlist containing only `query_tool`.
- Run the agent with the most restrictive permission mode available, so that no tool call can trigger a prompt or side effect.
- The tool runs with read-only database credentials. The verifier's tool is pointed at the cache and section data, not at any explainer output other than the commentary it is given.

**Limits** (set in `config/tolerances.yaml` or a runtime config):
- Maximum turns per call, maximum queries per call, maximum rows per query, maximum tokens, and a wall-clock timeout.
- Hitting a limit is a failure and follows the retry-once-then-stop rule.

**Structured output**
- Request JSON conforming to the section schema, using structured output if the SDK supports it.
- Always validate with `jsonschema` or `pydantic` in `run_pack.py` as well, regardless of SDK support.

**Audit log**
- Use hooks or message streaming to write every prompt, tool call, tool result and final response to `runs/<id>/audit/`, tagged by section and agent.
- The audit log is part of the immutable run folder.

**Skills loading**
- Place skills in the location the SDK reads skills from, or pass their content into the system prompt. Either way, the verifier's session must not have access to `cio-report-style` as writing guidance, and must not see the explainer's prompt.

**Credentials and secrets**
- API keys and read-only data credentials come from the environment or a secrets manager. Never write them to the repository, the audit log or the run folder.

## 8. Skills (reference files)

Skills hold knowledge, not control flow. Do not use them to sequence the run. Orchestration lives only in `run_pack.py`.

- **`skills/pnl-definitions.md`:** meaning of PnL, each attribution bucket, sign conventions, pod grouping, treatment of holidays and restatements, metric formulas. **[CONFIRM: content from the owner]**
- **`skills/cio-report-style.md`:** tone, length, how to state uncertainty, when to say "no clear driver found", banned phrases, size thresholds for words like "sharply" and "significant", and one or two example outputs.

Both agents may read `pnl-definitions`. Only the explainer loads `cio-report-style` as writing guidance. The verifier uses its thresholds only for calibration checks.

An optional later skill, `run-cio-pack`, may tell a chat agent how to invoke `python run_pack.py <month>` and interpret its exit codes. It describes how to call the script and does not contain the pipeline.

## 9. Failure behaviour

| Situation | Behaviour |
|---|---|
| API error or missing partition | Stop at load, name the source and date |
| Reconciliation break | Stop at checks, print pod, date and difference |
| Schema-invalid explainer output | Retry once, then stop |
| Unverifiable figure in commentary | Stop at code verifier, print the sentence |
| Unsupported claim from verifier agent | One retry through the explainer, then stop (or flag, if enabled) |
| Verifier output schema-invalid | Retry once, then stop |
| Agent hits a runtime limit (turns, queries, tokens, time) | Retry once, then stop |
| Template slot empty | Stop at render |

## 10. Acceptance tests

Run on one past closed month that the owner knows well **[CONFIRM: which month]**.

1. `python run_pack.py <month>` completes with no manual steps.
2. Every pod's month PnL in the pack equals the official figure.
3. Two runs on the same data give identical numbers and structure.
4. Corrupting one number in the cache makes the run **stop at a check**.
5. Altering a figure in an explainer output makes the **code verifier reject** it.
6. Injecting an unsupported causal claim (for example, "driven by the payrolls release") into a commentary makes the **verifier agent** mark it `unsupported`.
7. Removing the largest contributor from a commentary makes the verifier agent report an **omission**.
8. S2 totals equal S1 totals.
9. The footer shows template and code versions, data as-of times, claims verified and retries.
10. An agent given a request that needs a file, shell or web tool cannot do it, because only `query_tool` is allowed.
11. The audit folder contains every prompt, tool call and response for the run.

Unit tests are required for: each reconciliation check, each metric formula (with hand-calculated cases), the number extractor in the code verifier, the formatting function, and the query tool's row cap and read-only enforcement.

## 11. Build order

1. Loaders and store, with manifests.
2. Coverage and reconciliation checks. **Do not continue until these pass on the test month.**
3. Section queries S1, S2 and S4, hand-checked against trusted figures.
4. Template and renderer, with no commentary yet.
5. Query tool and `agents/runtime.py`, with permissions locked down and the audit log working.
6. Explainer, commentary schemas and skills.
7. Code verifier.
8. Verifier agent, verdict schema and the retry loop.
9. `run_pack.py` wire-up, failure behaviour and acceptance tests.

A reasonable stopping point for a first demo is after step 4: a trustworthy, consistently formatted pack with no LLM involved. Add the explainer, then the verifier agent, afterwards.

## 12. Metrics to record from every run

- Explainer retries per section (a high rate suggests the prompt or style skill needs work).
- Verdict counts by type and the most common unsupported-claim patterns.
- Time and token use per stage.
- Any code-verifier failures.

These tell the owner whether the second agent earns its cost.

## 13. Constraints

- All data stays within our infrastructure. Position-level and PnL data must not be sent to any external service. The model endpoint must be one approved by the owner **[CONFIRM]**.
- Use read-only API credentials.
- No secrets in the repository.
- Do not add features outside section 3 without approval.

## 14. Later increments (do not build now)

Risk and trade loaders, then S3 expected-vs-actual, S5 cross-pod netting and S6 top and bottom trades. After that: parallel per-section agent workflows, scheduling, a memory layer and a chat Q&A agent for ad hoc questions (where model-driven orchestration with subagents becomes appropriate).

## 15. Open questions for the owner

1. Exact API fields, grain, sign and currency conventions for PnL and attribution.
2. Attribution bucket names and any pod-specific variations.
3. Official PnL source used for reconciliation, and tolerances.
4. Holiday calendar and trailing re-pull window.
5. Metric definitions: annualisation factor, drawdown basis, hit rate.
6. Which past month to use as the test month.
7. Approved model endpoint and data handling policy.
8. Agent runtime: Claude Agent SDK or the Claude API directly.
9. Where the store lives, and who has read access.
10. If a claim stays unsupported after the retry: stop the run, or render without it and flag it?
