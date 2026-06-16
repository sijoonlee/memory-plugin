#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"

echo "Setting up Memory MCP Claude Code plugin..."
echo

"${INSTALL_DIR}/scripts/register-claude-plugin.sh"

echo
"${INSTALL_DIR}/scripts/download-embedding-model.sh"

# Hooks ship with the plugin manifest (.claude-plugin/plugin.json -> "hooks":
# "./hooks/claude-hooks.json"), so Claude Code loads them on install with
# ${CLAUDE_PLUGIN_ROOT} resolved automatically. No settings.json merge needed.

echo
echo "Current Memory MCP status:"
"${INSTALL_DIR}/scripts/check-memory-status.sh"

echo
echo "Memory MCP setup complete."
echo "Start a new Claude Code session so plugin skills, MCP config, and hooks are reloaded."
