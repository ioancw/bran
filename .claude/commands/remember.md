---
description: Pin a fact or rule to this project's persistent memory so future chats see it automatically.
argument-hint: <what to remember>
---

The user wants to pin this to the current project's persistent memory:

**$ARGUMENTS**

Refine it into a clear, self-contained memory entry — typically one or two declarative sentences. Phrase as a fact, rule, or preference. If similar memory already appears in the project's existing memory (shown in your system prompt), prefer rewording the existing entry rather than duplicating.

Then call `mcp__bran__save_project_memory` to persist. Use the `project_id` from your system prompt — do NOT invent one.

After saving, confirm in one short sentence what you wrote — don't repeat the full text back, just acknowledge.
