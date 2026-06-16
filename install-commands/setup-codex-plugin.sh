#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"
HOOK_SOURCE="${PROJECT_ROOT}/hooks/codex-hooks.json"
HOOK_TARGET="${PROJECT_ROOT}/.codex/hooks.json"

echo "Setting up Memory MCP Codex plugin..."
echo

"${INSTALL_DIR}/scripts/install-codex-plugin.sh"

echo
"${INSTALL_DIR}/scripts/download-embedding-model.sh"

echo
echo "Registering Codex hooks for review..."
if [[ ! -f "${HOOK_SOURCE}" ]]; then
  echo "Missing hook config: ${HOOK_SOURCE}" >&2
  exit 1
fi

mkdir -p "$(dirname "${HOOK_TARGET}")"
if [[ -f "${HOOK_TARGET}" ]] && ! cmp -s "${HOOK_SOURCE}" "${HOOK_TARGET}"; then
  BACKUP_PATH="${HOOK_TARGET}.bak.$(date -u +%Y%m%d%H%M%S)"
  cp "${HOOK_TARGET}" "${BACKUP_PATH}"
  echo "Backed up existing hook config to ${BACKUP_PATH}"
fi
cp "${HOOK_SOURCE}" "${HOOK_TARGET}"
echo "Hook config staged at ${HOOK_TARGET}"
echo "Codex may ask you to review these hooks before they run."

echo
echo "Current Memory MCP status:"
"${INSTALL_DIR}/scripts/check-memory-status.sh"

echo
echo "Memory MCP setup complete."
echo "Start a new Codex thread/session so plugin skills, MCP config, and hooks are reloaded."
