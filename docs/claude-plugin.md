# Claude Code Plugin Packaging

This repository can be installed as a local Claude Code plugin. The packaging is
kept separate from the Codex plugin, but reuses the agent-agnostic MCP server,
skill, and helper scripts.

## Structure

```text
.claude-plugin/plugin.json  plugin manifest
.mcp.json                   MCP server registration (shared with Codex)
skills/memory-mcp/          agent-facing usage guidance (shared)
hooks/claude-hooks.json     event-capture hook config for Claude Code
install-commands/setup-claude-plugin.sh  guided all-in-one setup
install-commands/uninstall-claude-plugin.sh  local plugin uninstall helper
install-commands/scripts/register-claude-plugin.sh  local Claude plugin registration helper
install-commands/scripts/download-embedding-model.sh  embedding model warmup helper (shared)
install-commands/scripts/check-memory-status.sh  status helper (shared)
install-commands/scripts/memory-mcp-server.sh  MCP server wrapper (shared)
```

The core Python app remains usable without installing the plugin.

## Setup

Run the guided setup command from any directory:

```bash
install-commands/setup-claude-plugin.sh
```

The setup command registers the local Claude Code plugin, downloads and warms
the embedding model, merges Claude Code hooks into `.claude/settings.json` for
review, and prints the current memory status.

## MCP Server Installation

The plugin manifest points to `.mcp.json`, which starts the server through:

```bash
install-commands/scripts/memory-mcp-server.sh
```

The wrapper resolves the project root from its own path and then runs:

```bash
uv --directory <project-root> run memory-mcp-server
```

Set `MEMORY_MCP_ROOT` to override the default local store path.

If you prefer to register the MCP server directly instead of through the plugin,
use the Claude Code CLI:

```bash
claude mcp add memory-mcp \
  --env MEMORY_MCP_ROOT=.memory-mcp \
  -- <project-root>/install-commands/scripts/memory-mcp-server.sh
```

A project-scoped `.mcp.json` snippet for Claude Code:

```json
{
  "mcpServers": {
    "memory-mcp": {
      "command": "${CLAUDE_PLUGIN_ROOT}/install-commands/scripts/memory-mcp-server.sh",
      "args": [],
      "env": {
        "MEMORY_MCP_ROOT": ".memory-mcp"
      }
    }
  }
}
```

`MEMORY_MCP_ROOT` selects the memory store root. A relative value resolves
against the project root, so project-scoped stores stay isolated per repo.

## Individual Commands

The setup command is the normal path. The lower-level commands remain available
for repair or debugging.

Download only the embedding model:

```bash
install-commands/scripts/download-embedding-model.sh
```

Register only the local Claude Code plugin:

```bash
install-commands/scripts/register-claude-plugin.sh
```

The plugin registration helper:

1. links this repo under `~/.claude/memory-mcp-marketplace/plugins/memory-mcp`
2. creates or updates `~/.claude/memory-mcp-marketplace/.claude-plugin/marketplace.json`,
   referencing the plugin as a relative `./plugins/memory-mcp` source (the format
   Claude Code expects for a local marketplace plugin)
3. runs `claude plugin marketplace add`, then `claude plugin marketplace update`
   to refresh the snapshot from disk, then `claude plugin install memory-mcp@memory-mcp-local`

Set these environment variables to override defaults:

```bash
PLUGIN_SOURCE_ROOT=/path/to/plugins \
MARKETPLACE_ROOT=/path/to/marketplace-root \
CLAUDE_BIN=/path/to/claude \
install-commands/scripts/register-claude-plugin.sh
```

Start a new Claude Code session after installation so plugin skills and MCP
config are reloaded.

## Event Capture

Hooks are packaged at `hooks/claude-hooks.json`. Unlike Codex, Claude Code reads
hooks from `.claude/settings.json`, so `install-commands/setup-claude-plugin.sh`
merges the packaged hook events into that file (backing up any existing settings)
instead of staging a standalone hooks file. Installing the plugin is not consent
to auto-enable hooks: the hook config is intentionally not referenced from
`.claude-plugin/plugin.json`, so review the merged hooks before relying on them.

The packaged commands map Claude Code lifecycle events to `memory-mcp-event`:

```text
UserPromptSubmit -> memory-mcp-event append --adapter claude --event-type user_prompt
PostToolUse      -> memory-mcp-event append --adapter claude --event-type tool_result
Stop             -> memory-mcp-event append --adapter claude --event-type turn_stop
```

The Claude adapter (`src/memory_mcp/adapters/claude.py`) reads `cwd` and
`session_id` from each hook payload to preserve project and session identifiers.
When `session_id` is absent it falls back to a stable `claude_hook:<project-name>`
identifier so unrelated sessions are not merged. Claude Code has no per-turn id,
so `run_id` stays unset and the session id carries grouping.

`${CLAUDE_PLUGIN_ROOT}` resolves to this repository — both the `uv` project that
runs the event CLI and the memory store root, so the hook store lands at
`<repo>/.memory-mcp`. That is the same store the MCP server uses (the server
wrapper resolves the default `MEMORY_MCP_ROOT=.memory-mcp` against the repo), so
captured events and retrieval share one store. Project scoping does not depend on
the store path: the adapter records each event's project from the payload `cwd`.
The setup helper substitutes `${CLAUDE_PLUGIN_ROOT}` with the absolute repo path
while merging into settings, because Claude Code only populates that variable for
plugin-provided hooks, not for the user `.claude/settings.json` hooks staged here.

## Uninstall

Run:

```bash
install-commands/uninstall-claude-plugin.sh
```

The uninstall helper removes the Claude Code plugin registration, removes the
`memory-mcp-local` marketplace entry, removes the `~/plugins/memory-mcp` symlink
when it points at this repo, and removes only the Memory MCP hook entries from
`.claude/settings.json`. It leaves `.memory-mcp` data untouched.

## Local Workflow

```bash
uv run memory-mcp status
uv run memory-mcp process
uv run memory-mcp review
```

`status` summarizes event backlog, sessions, candidates, and memory counts.
`process` consumes events, refreshes session segments, proposes candidates, and
applies decay. `review` opens the local review UI for human approval or rejection
of candidates. See `skills/memory-mcp/SKILL.md` for when to search memory, create
explicit memory, and send feedback.
