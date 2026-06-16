#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UV_BIN="${UV_BIN:-uv}"

echo "Warming local embedding model..."
"${UV_BIN}" --directory "${PROJECT_ROOT}" run memory-mcp install-model
