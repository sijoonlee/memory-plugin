#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="memory-mcp"
MARKETPLACE_NAME="memory-mcp-local"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MARKETPLACE_ROOT="${MARKETPLACE_ROOT:-${HOME}/.claude/memory-mcp-marketplace}"
MARKETPLACE_FILE="${MARKETPLACE_ROOT}/.claude-plugin/marketplace.json"
# Claude Code resolves a plugin's `source` string relative to the marketplace
# root, so link the plugin under <marketplace>/plugins/<name> (mirroring the
# official marketplace layout) and reference it as ./plugins/<name>.
PLUGIN_SOURCE_ROOT="${PLUGIN_SOURCE_ROOT:-${MARKETPLACE_ROOT}/plugins}"
PLUGIN_LINK="${PLUGIN_SOURCE_ROOT}/${PLUGIN_NAME}"
PLUGIN_REL_SOURCE="./plugins/${PLUGIN_NAME}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"

realpath_portable() {
  python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

PROJECT_REALPATH="$(realpath_portable "${PROJECT_ROOT}")"

if [[ ! -f "${PROJECT_ROOT}/.claude-plugin/plugin.json" ]]; then
  echo "Missing .claude-plugin/plugin.json in ${PROJECT_ROOT}" >&2
  exit 1
fi

mkdir -p "${PLUGIN_SOURCE_ROOT}"

if [[ -L "${PLUGIN_LINK}" ]]; then
  LINK_TARGET="$(realpath_portable "${PLUGIN_LINK}")"
  if [[ "${LINK_TARGET}" != "${PROJECT_REALPATH}" ]]; then
    echo "${PLUGIN_LINK} already points to ${LINK_TARGET}" >&2
    echo "Remove or update it before installing ${PLUGIN_NAME}." >&2
    exit 1
  fi
elif [[ -e "${PLUGIN_LINK}" ]]; then
  LINK_TARGET="$(realpath_portable "${PLUGIN_LINK}")"
  if [[ "${LINK_TARGET}" != "${PROJECT_REALPATH}" ]]; then
    echo "${PLUGIN_LINK} already exists and is not this repo: ${LINK_TARGET}" >&2
    exit 1
  fi
else
  ln -s "${PROJECT_ROOT}" "${PLUGIN_LINK}"
fi

mkdir -p "$(dirname "${MARKETPLACE_FILE}")"

MARKETPLACE_FILE="${MARKETPLACE_FILE}" \
PLUGIN_NAME="${PLUGIN_NAME}" \
MARKETPLACE_NAME="${MARKETPLACE_NAME}" \
PLUGIN_REL_SOURCE="${PLUGIN_REL_SOURCE}" \
python3 <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MARKETPLACE_FILE"]).expanduser()
plugin_name = os.environ["PLUGIN_NAME"]
marketplace_name = os.environ["MARKETPLACE_NAME"]
plugin_source = os.environ["PLUGIN_REL_SOURCE"]

if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
else:
    payload = {
        "name": marketplace_name,
        "owner": {"name": "Memory MCP"},
        "plugins": [],
    }

if not isinstance(payload, dict):
    raise SystemExit(f"{path} must contain a JSON object")

payload.setdefault("name", marketplace_name)
payload.setdefault("owner", {"name": "Memory MCP"})
plugins = payload.setdefault("plugins", [])
if not isinstance(plugins, list):
    raise SystemExit(f"{path} field `plugins` must be an array")

entry = {
    "name": plugin_name,
    "source": plugin_source,
    "description": (
        "Local reusable memory retrieval, feedback, and review workflow "
        "for Claude Code."
    ),
}

for index, existing in enumerate(plugins):
    if isinstance(existing, dict) and existing.get("name") == plugin_name:
        plugins[index] = entry
        break
else:
    plugins.append(entry)

path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "Plugin source: ${PLUGIN_LINK} -> ${PROJECT_ROOT}"
echo "Marketplace:   ${MARKETPLACE_FILE}"
echo "Installing:    ${PLUGIN_NAME}@${MARKETPLACE_NAME}"

# Add the marketplace if it is new, then always refresh its snapshot from disk:
# `marketplace add` is a no-op once registered, so without an explicit update the
# install would read a stale snapshot and miss marketplace.json edits.
"${CLAUDE_BIN}" plugin marketplace add "${MARKETPLACE_ROOT}" 2>/dev/null || true
"${CLAUDE_BIN}" plugin marketplace update "${MARKETPLACE_NAME}" 2>/dev/null || true

"${CLAUDE_BIN}" plugin install "${PLUGIN_NAME}@${MARKETPLACE_NAME}"

echo
echo "Installed ${PLUGIN_NAME}. Start a new Claude Code session so skills and MCP config are reloaded."
