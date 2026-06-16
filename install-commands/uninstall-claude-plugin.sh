#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="memory-mcp"
MARKETPLACE_NAME="memory-mcp-local"
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"
MARKETPLACE_ROOT="${MARKETPLACE_ROOT:-${HOME}/.claude/memory-mcp-marketplace}"
MARKETPLACE_FILE="${MARKETPLACE_ROOT}/.claude-plugin/marketplace.json"
PLUGIN_SOURCE_ROOT="${PLUGIN_SOURCE_ROOT:-${MARKETPLACE_ROOT}/plugins}"
PLUGIN_LINK="${PLUGIN_SOURCE_ROOT}/${PLUGIN_NAME}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
SETTINGS_TARGET="${PROJECT_ROOT}/.claude/settings.json"

realpath_portable() {
  python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

PROJECT_REALPATH="$(realpath_portable "${PROJECT_ROOT}")"

echo "Uninstalling Memory MCP Claude Code plugin..."
echo

if command -v "${CLAUDE_BIN}" >/dev/null 2>&1; then
  echo "Removing Claude Code plugin registration..."
  if ! "${CLAUDE_BIN}" plugin uninstall "${PLUGIN_NAME}@${MARKETPLACE_NAME}" 2>/dev/null; then
    if ! "${CLAUDE_BIN}" plugin uninstall "${PLUGIN_NAME}" 2>/dev/null; then
      echo "Claude plugin removal did not complete. Continuing local cleanup." >&2
    fi
  fi
  "${CLAUDE_BIN}" plugin marketplace remove "${MARKETPLACE_NAME}" 2>/dev/null || true
else
  echo "Claude CLI not found at '${CLAUDE_BIN}'. Skipping Claude plugin removal." >&2
fi

if [[ -f "${MARKETPLACE_FILE}" ]]; then
  echo "Removing marketplace entry from ${MARKETPLACE_FILE}..."
  MARKETPLACE_FILE="${MARKETPLACE_FILE}" PLUGIN_NAME="${PLUGIN_NAME}" python3 <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MARKETPLACE_FILE"]).expanduser()
plugin_name = os.environ["PLUGIN_NAME"]
payload = json.loads(path.read_text(encoding="utf-8"))

if not isinstance(payload, dict):
    raise SystemExit(f"{path} must contain a JSON object")

plugins = payload.get("plugins", [])
if not isinstance(plugins, list):
    raise SystemExit(f"{path} field `plugins` must be an array")

payload["plugins"] = [
    entry
    for entry in plugins
    if not (isinstance(entry, dict) and entry.get("name") == plugin_name)
]

path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
else
  echo "Marketplace file not found: ${MARKETPLACE_FILE}"
fi

if [[ -L "${PLUGIN_LINK}" ]]; then
  LINK_TARGET="$(realpath_portable "${PLUGIN_LINK}")"
  if [[ "${LINK_TARGET}" == "${PROJECT_REALPATH}" ]]; then
    echo "Removing plugin symlink ${PLUGIN_LINK}..."
    rm "${PLUGIN_LINK}"
  else
    echo "Leaving ${PLUGIN_LINK}; it points to ${LINK_TARGET}, not this repo." >&2
  fi
elif [[ -e "${PLUGIN_LINK}" ]]; then
  LINK_TARGET="$(realpath_portable "${PLUGIN_LINK}")"
  echo "Leaving ${PLUGIN_LINK}; it is not a symlink. Target: ${LINK_TARGET}" >&2
else
  echo "Plugin symlink not found: ${PLUGIN_LINK}"
fi

if [[ -f "${SETTINGS_TARGET}" ]]; then
  echo "Removing Memory MCP hooks from ${SETTINGS_TARGET}..."
  SETTINGS_TARGET="${SETTINGS_TARGET}" python3 <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["SETTINGS_TARGET"]).expanduser()
settings = json.loads(path.read_text(encoding="utf-8"))

if not isinstance(settings, dict):
    raise SystemExit(f"{path} must contain a JSON object")


def is_memory_entry(entry):
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []):
        command = hook.get("command", "") if isinstance(hook, dict) else ""
        if "memory-mcp-event append" in command and "--adapter claude" in command:
            return True
    return False


hooks = settings.get("hooks")
if isinstance(hooks, dict):
    for event in list(hooks):
        kept = [entry for entry in hooks[event] if not is_memory_entry(entry)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)

path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
PY
else
  echo "Claude settings not found: ${SETTINGS_TARGET}"
fi

echo
echo "Memory MCP plugin uninstall complete."
echo "Local memory data under ${PROJECT_ROOT}/.memory-mcp was left untouched."
echo "Start a new Claude Code session so plugin state is refreshed."
