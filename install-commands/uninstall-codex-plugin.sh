#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="memory-mcp"
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"
PLUGIN_SOURCE_ROOT="${PLUGIN_SOURCE_ROOT:-${HOME}/plugins}"
PLUGIN_LINK="${PLUGIN_SOURCE_ROOT}/${PLUGIN_NAME}"
MARKETPLACE_PATH="${MARKETPLACE_PATH:-${HOME}/.agents/plugins/marketplace.json}"
CODEX_BIN="${CODEX_BIN:-codex}"
HOOK_SOURCE="${PROJECT_ROOT}/hooks/codex-hooks.json"
HOOK_TARGET="${PROJECT_ROOT}/.codex/hooks.json"

realpath_portable() {
  python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

PROJECT_REALPATH="$(realpath_portable "${PROJECT_ROOT}")"

echo "Uninstalling Memory MCP Codex plugin..."
echo

if command -v "${CODEX_BIN}" >/dev/null 2>&1; then
  echo "Removing Codex plugin registration..."
  if ! "${CODEX_BIN}" plugin remove "${PLUGIN_NAME}"; then
    echo "Codex plugin removal did not complete. Continuing local cleanup." >&2
  fi
else
  echo "Codex CLI not found at '${CODEX_BIN}'. Skipping Codex plugin removal." >&2
fi

if [[ -f "${MARKETPLACE_PATH}" ]]; then
  echo "Removing marketplace entry from ${MARKETPLACE_PATH}..."
  MARKETPLACE_PATH="${MARKETPLACE_PATH}" PLUGIN_NAME="${PLUGIN_NAME}" python3 <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MARKETPLACE_PATH"]).expanduser()
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
  echo "Marketplace file not found: ${MARKETPLACE_PATH}"
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

if [[ -f "${HOOK_TARGET}" ]]; then
  # setup-codex-plugin.sh stages the hooks with ${CODEX_PLUGIN_ROOT} replaced by
  # this repo's absolute path, so compare against the rendered form, not the raw
  # packaged source.
  if [[ -f "${HOOK_SOURCE}" ]] && \
    HOOK_SOURCE="${HOOK_SOURCE}" \
    HOOK_TARGET="${HOOK_TARGET}" \
    PROJECT_ROOT="${PROJECT_ROOT}" \
    python3 <<'PY'
import os
import sys
from pathlib import Path

repo = os.environ["PROJECT_ROOT"]
rendered = (
    Path(os.environ["HOOK_SOURCE"])
    .read_text(encoding="utf-8")
    .replace("${CODEX_PLUGIN_ROOT}", repo)
)
target = Path(os.environ["HOOK_TARGET"]).read_text(encoding="utf-8")
sys.exit(0 if target == rendered else 1)
PY
  then
    echo "Removing staged hook config ${HOOK_TARGET}..."
    rm "${HOOK_TARGET}"
    rmdir "$(dirname "${HOOK_TARGET}")" 2>/dev/null || true
  else
    echo "Leaving ${HOOK_TARGET}; it differs from packaged Memory MCP hooks." >&2
  fi
else
  echo "Staged hook config not found: ${HOOK_TARGET}"
fi

echo
echo "Memory MCP plugin uninstall complete."
echo "Local memory data under ${PROJECT_ROOT}/.memory-mcp was left untouched."
echo "Start a new Codex thread/session so plugin state is refreshed."
