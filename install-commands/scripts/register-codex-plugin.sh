#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="memory-mcp"
PLUGIN_CATEGORY="Productivity"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PLUGIN_SOURCE_ROOT="${PLUGIN_SOURCE_ROOT:-${HOME}/plugins}"
PLUGIN_LINK="${PLUGIN_SOURCE_ROOT}/${PLUGIN_NAME}"
MARKETPLACE_PATH="${MARKETPLACE_PATH:-${HOME}/.agents/plugins/marketplace.json}"
CODEX_BIN="${CODEX_BIN:-codex}"

realpath_portable() {
  python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}

PROJECT_REALPATH="$(realpath_portable "${PROJECT_ROOT}")"

if [[ ! -f "${PROJECT_ROOT}/.codex-plugin/plugin.json" ]]; then
  echo "Missing .codex-plugin/plugin.json in ${PROJECT_ROOT}" >&2
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

mkdir -p "$(dirname "${MARKETPLACE_PATH}")"

MARKETPLACE_NAME="$(
  MARKETPLACE_PATH="${MARKETPLACE_PATH}" \
  PLUGIN_NAME="${PLUGIN_NAME}" \
  PLUGIN_CATEGORY="${PLUGIN_CATEGORY}" \
  python3 <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MARKETPLACE_PATH"]).expanduser()
plugin_name = os.environ["PLUGIN_NAME"]
category = os.environ["PLUGIN_CATEGORY"]

if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
else:
    payload = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [],
    }

if not isinstance(payload, dict):
    raise SystemExit(f"{path} must contain a JSON object")

marketplace_name = payload.setdefault("name", "personal")
payload.setdefault("interface", {"displayName": "Personal"})
plugins = payload.setdefault("plugins", [])
if not isinstance(plugins, list):
    raise SystemExit(f"{path} field `plugins` must be an array")

entry = {
    "name": plugin_name,
    "source": {
        "source": "local",
        "path": f"./plugins/{plugin_name}",
    },
    "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    },
    "category": category,
}

for index, existing in enumerate(plugins):
    if isinstance(existing, dict) and existing.get("name") == plugin_name:
        plugins[index] = entry
        break
else:
    plugins.append(entry)

path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(marketplace_name)
PY
)"

echo "Plugin source: ${PLUGIN_LINK} -> ${PROJECT_ROOT}"
echo "Marketplace:   ${MARKETPLACE_PATH}"
echo "Installing:    ${PLUGIN_NAME}@${MARKETPLACE_NAME}"

"${CODEX_BIN}" plugin add "${PLUGIN_NAME}@${MARKETPLACE_NAME}"

echo
echo "Installed ${PLUGIN_NAME}. Start a new Codex thread/session so skills and MCP config are reloaded."
