---
name: memory-mcp
description: Use local Memory MCP when prior project context, reusable lessons, memory feedback, or memory operator workflow may help a coding task.
---

# Memory MCP

Use Memory MCP to retrieve and maintain compact, reusable lessons from prior work.

## When to search

Search memory when any of these are true:

- The task may depend on prior project conventions, commands, paths, or pitfalls.
- The user asks about previous work or says to use remembered context.
- You are about to make a broad edit and prior lessons could reduce risk.

Use `memory_search` with a concise query describing the current task. Keep `limit` small unless the user asks for broader context.

## When to create memory

Create explicit memories only for durable reusable lessons. Good memories explain:

- what happened or what was learned
- when it will be useful later
- what action to take next time

Do not create memories for raw logs, temporary status, secrets, vague advice, or details already obvious from checked-in docs.

## Feedback

Call `memory_feedback` only for memories you actually considered.

- Use `used` when a memory changed your plan, command, edit, or answer.
- Use `helpful` when it clearly improved the result or the user confirmed it.
- Use `not_helpful` when it looked relevant but did not help.
- Use `stale`, `incorrect`, or `contradicted` when the memory should be demoted or retired.

Do not send feedback for every returned memory automatically.

## Inspecting the store

When the user asks what is stored or about store health, use the read-only inspection tools instead of switching to a terminal:

- `memory_status` reports event backlog, session segments, candidate counts, and memory counts by status.
- `memory_list` browses stored memories by status (`active`, `stale`, ...); this is a listing, not the semantic `memory_search`.
- `candidate_list` lists pipeline-proposed candidates by status (`pending_review`, ...).

These are read-only. Call them only when the user asks to see what exists or wants a health check, not during normal task work. Approving, rejecting, or processing candidates still happens through the operator workflow below.

## Operator workflow

Use these commands from the plugin/project root:

```bash
uv run memory-mcp status
uv run memory-mcp process
uv run memory-mcp review
```

`status` summarizes event backlog, sessions, candidates, and memory counts. `process` consumes events, refreshes session segments, proposes candidates, and applies decay. `review` opens the local review UI for human approval or rejection of candidates.

Hook configuration is packaged per agent under `hooks/codex-hooks.json` and `hooks/claude-hooks.json`, but installing the plugin should not be treated as consent to auto-enable hooks. Enable or merge hooks deliberately.
