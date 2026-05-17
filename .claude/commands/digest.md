---
description: Produce a structured daily digest on a topic by dispatching the research subagent and then summarising.
argument-hint: <topic>
---

Build a daily digest on the topic: **$ARGUMENTS**

Procedure:
1. Use the `research` subagent (Agent tool) to gather the latest reputable information on this topic from the last 24–48 hours.
2. Once it returns, hand the result to the `summariser` subagent and ask it to produce a briefing in this format:
   - **TL;DR** (one sentence)
   - **Top stories** (3–5 bullets, each with a citation)
   - **Why it matters** (one short paragraph)
   - **Sources** (URLs from the research agent)

Return the briefing as your final message — do not summarise it further.
