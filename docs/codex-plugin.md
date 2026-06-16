# Codex Plugin Packaging

This repository is the Memory MCP plugin root.

## Structure

```text
.codex-plugin/plugin.json  plugin manifest
.mcp.json                  MCP server registration
skills/memory-mcp/         Codex-facing usage guidance
hooks/codex-hooks.json     event-capture hook config
install-commands/setup-codex-plugin.sh  guided all-in-one setup
install-commands/uninstall-codex-plugin.sh  local plugin uninstall helper
install-commands/scripts/download-embedding-model.sh  embedding model warmup helper
install-commands/scripts/register-codex-plugin.sh  local Codex plugin registration helper
install-commands/scripts/check-memory-status.sh  status helper
```

The core Python app remains usable without installing the plugin.

## Setup

Run the guided setup command from any directory:

```bash
install-commands/setup-codex-plugin.sh
```

The setup command installs the local Codex plugin, downloads and warms the
embedding model, stages Codex hooks for review, and prints the current memory
status.

## Individual Commands

The setup command is the normal path. The lower-level commands remain available
for repair or debugging.

Download only the embedding model:

```bash
install-commands/scripts/download-embedding-model.sh
```

Register only the local Codex plugin:

```bash
install-commands/scripts/register-codex-plugin.sh
```

The plugin registration helper:

1. creates `~/plugins/memory-mcp` as a symlink to this repo
2. creates or updates `~/.agents/plugins/marketplace.json`
3. runs `codex plugin add memory-mcp@<marketplace-name>`

Set these environment variables to override defaults:

```bash
PLUGIN_SOURCE_ROOT=/path/to/plugins \
MARKETPLACE_PATH=/path/to/marketplace.json \
CODEX_BIN=/path/to/codex \
install-commands/scripts/register-codex-plugin.sh
```

Start a new Codex thread/session after installation so plugin skills and MCP config are reloaded.

## Uninstall

Run:

```bash
install-commands/uninstall-codex-plugin.sh
```

The uninstall helper removes the Codex plugin registration, removes the
`memory-mcp` marketplace entry, removes the `~/plugins/memory-mcp` symlink when
it points at this repo, and removes `.codex/hooks.json` only when it matches the
packaged Memory MCP hook config. It leaves `.memory-mcp` data untouched.

## MCP Server

The plugin manifest points to `.mcp.json`, which starts the server through:

```bash
install-commands/scripts/memory-mcp-server.sh
```

The wrapper resolves the project root from its own path and then runs:

```bash
uv --directory <project-root> run memory-mcp-server
```

Set `MEMORY_MCP_ROOT` to override the default local store path.

## Hooks

Hooks are packaged at `hooks/codex-hooks.json`. `install-commands/setup-codex-plugin.sh`
stages that config at `.codex/hooks.json` so Codex can review it before use.

The packaged commands use a `${CODEX_PLUGIN_ROOT}` placeholder for the repo path.
Codex does not expand environment variables in hook commands, so the setup helper
replaces `${CODEX_PLUGIN_ROOT}` with this repo's absolute path while staging. This
removes the previous `git rev-parse` lookup (which assumed the hook always ran
inside this repo and failed outside a git checkout) and keeps the hook event store
at `<repo>/.memory-mcp`, the same store the MCP server uses. The Codex adapter
still records each event's project from the hook payload `cwd`, so project scoping
is preserved even though the store location is fixed.

If you wire the hooks up by hand instead of running the setup helper, replace
`${CODEX_PLUGIN_ROOT}` with the absolute path to this repository yourself.

## Local Workflow

```bash
uv run memory-mcp status
uv run memory-mcp process
uv run memory-mcp review
```
