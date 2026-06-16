#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MEMORY_MCP_ROOT="${MEMORY_MCP_ROOT:-${PROJECT_ROOT}/.memory-mcp}"
UV_BIN="${UV_BIN:-uv}"

case "${MEMORY_MCP_ROOT}" in
  /*) ;;
  *) MEMORY_MCP_ROOT="${PROJECT_ROOT}/${MEMORY_MCP_ROOT}" ;;
esac

export MEMORY_MCP_ROOT
exec "${UV_BIN}" --directory "${PROJECT_ROOT}" run memory-mcp-server
