#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"
HOOK_SOURCE="${PROJECT_ROOT}/hooks/claude-hooks.json"
SETTINGS_TARGET="${PROJECT_ROOT}/.claude/settings.json"

echo "Setting up Memory MCP Claude Code plugin..."
echo

"${INSTALL_DIR}/scripts/register-claude-plugin.sh"

echo
"${INSTALL_DIR}/scripts/download-embedding-model.sh"

echo
echo "Merging Claude Code hooks into ${SETTINGS_TARGET} for review..."
if [[ ! -f "${HOOK_SOURCE}" ]]; then
  echo "Missing hook config: ${HOOK_SOURCE}" >&2
  exit 1
fi

mkdir -p "$(dirname "${SETTINGS_TARGET}")"

HOOK_SOURCE="${HOOK_SOURCE}" \
SETTINGS_TARGET="${SETTINGS_TARGET}" \
PROJECT_ROOT="${PROJECT_ROOT}" \
python3 <<'PY'
import json
import os
import shutil
import time
from pathlib import Path

repo = os.environ["PROJECT_ROOT"]
source = Path(os.environ["HOOK_SOURCE"])
target = Path(os.environ["SETTINGS_TARGET"])


def substitute(value):
    if isinstance(value, str):
        return value.replace("${CLAUDE_PLUGIN_ROOT}", repo)
    if isinstance(value, list):
        return [substitute(item) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item) for key, item in value.items()}
    return value


def is_memory_entry(entry):
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []):
        command = hook.get("command", "") if isinstance(hook, dict) else ""
        if "memory-mcp-event append" in command and "--adapter claude" in command:
            return True
    return False


packaged_hooks = substitute(json.loads(source.read_text(encoding="utf-8"))).get(
    "hooks", {}
)

if target.exists():
    settings = json.loads(target.read_text(encoding="utf-8"))
    original = target.read_text(encoding="utf-8")
else:
    settings = {}
    original = None

if not isinstance(settings, dict):
    raise SystemExit(f"{target} must contain a JSON object")

hooks = settings.setdefault("hooks", {})
if not isinstance(hooks, dict):
    raise SystemExit(f"{target} field `hooks` must be an object")

for event, entries in packaged_hooks.items():
    existing = [e for e in hooks.get(event, []) if not is_memory_entry(e)]
    existing.extend(entries)
    hooks[event] = existing

rendered = json.dumps(settings, indent=2) + "\n"
if original is not None and original != rendered:
    backup = f"{target}.bak.{time.strftime('%Y%m%d%H%M%S', time.gmtime())}"
    shutil.copyfile(target, backup)
    print(f"Backed up existing settings to {backup}")

target.write_text(rendered, encoding="utf-8")
print(f"Hooks merged into {target}")
PY

echo "Review the merged hooks before relying on them; Claude Code may ask to confirm."

echo
echo "Current Memory MCP status:"
"${INSTALL_DIR}/scripts/check-memory-status.sh"

echo
echo "Memory MCP setup complete."
echo "Start a new Claude Code session so plugin skills, MCP config, and hooks are reloaded."
