#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"
HOOK_ROOT="${CODEX_HOOK_ROOT:-${PWD}}"
HOOK_SOURCE="${PROJECT_ROOT}/hooks/codex-hooks.json"
HOOK_TARGET="${HOOK_ROOT}/.codex/hooks.json"
MCP_SERVER_SCRIPT="${PROJECT_ROOT}/install-commands/scripts/memory-mcp-server.sh"
CODEX_BIN="${CODEX_BIN:-codex}"

echo "Setting up Memory MCP Codex plugin..."
echo

"${INSTALL_DIR}/scripts/register-codex-plugin.sh"

echo
"${INSTALL_DIR}/scripts/download-embedding-model.sh"

echo
echo "Registering Codex MCP server..."
if [[ ! -x "${MCP_SERVER_SCRIPT}" ]]; then
  echo "Missing executable MCP server wrapper: ${MCP_SERVER_SCRIPT}" >&2
  exit 1
fi

UV_BIN_PATH="$(command -v uv || true)"
if [[ -z "${UV_BIN_PATH}" ]]; then
  echo "uv was not found on PATH. Install uv or set UV_BIN before running setup." >&2
  exit 1
fi

if "${CODEX_BIN}" mcp get memory-mcp >/dev/null 2>&1; then
  "${CODEX_BIN}" mcp remove memory-mcp
fi

"${CODEX_BIN}" mcp add memory-mcp \
  --env "MEMORY_MCP_ROOT=${PROJECT_ROOT}/.memory-mcp" \
  --env "UV_BIN=${UV_BIN_PATH}" \
  -- "${MCP_SERVER_SCRIPT}"

echo
echo "Registering Codex hooks for review..."
if [[ ! -f "${HOOK_SOURCE}" ]]; then
  echo "Missing hook config: ${HOOK_SOURCE}" >&2
  exit 1
fi

mkdir -p "$(dirname "${HOOK_TARGET}")"

# Codex does not expand environment variables in hook commands, so the packaged
# ${CODEX_PLUGIN_ROOT} placeholder is replaced with this repo's absolute path at
# stage time. The same is done for ${CODEX_UV_BIN}, because hook environments
# may not inherit the interactive shell PATH. This keeps the hook store aligned
# with the MCP server store and avoids the fragile `git rev-parse` lookup at runtime.
HOOK_SOURCE="${HOOK_SOURCE}" \
HOOK_TARGET="${HOOK_TARGET}" \
PROJECT_ROOT="${PROJECT_ROOT}" \
UV_BIN_PATH="${UV_BIN_PATH}" \
python3 <<'PY'
import os
import shutil
import time
from pathlib import Path

repo = os.environ["PROJECT_ROOT"]
uv_bin = os.environ["UV_BIN_PATH"]
source = Path(os.environ["HOOK_SOURCE"])
target = Path(os.environ["HOOK_TARGET"])

rendered = (
    source.read_text(encoding="utf-8")
    .replace("${CODEX_PLUGIN_ROOT}", repo)
    .replace("${CODEX_UV_BIN}", uv_bin)
)

if target.exists() and target.read_text(encoding="utf-8") != rendered:
    backup = f"{target}.bak.{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    shutil.copyfile(target, backup)
    print(f"Backed up existing hook config to {backup}")

target.write_text(rendered, encoding="utf-8")
PY
echo "Hook config staged at ${HOOK_TARGET}"
echo "Codex may ask you to review these hooks before they run."

echo
echo "Current Memory MCP status:"
"${INSTALL_DIR}/scripts/check-memory-status.sh"

echo
echo "Memory MCP setup complete."
echo "Start a new Codex thread/session so plugin skills, MCP config, and hooks are reloaded."
