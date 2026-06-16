from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_json(relative: str) -> dict:
    return json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))


def test_plugin_manifest_is_valid() -> None:
    manifest = _load_json(".claude-plugin/plugin.json")
    assert manifest["name"] == "memory-mcp"
    assert manifest["version"]
    assert "claude-code" in manifest["keywords"]
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["skills"] == "./skills/"


def test_plugin_manifest_does_not_auto_enable_hooks() -> None:
    # Hooks run commands automatically, so installing the plugin must not wire
    # them in. The manifest must not reference the packaged hook config.
    raw = (REPO_ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    assert "hooks" not in json.loads(raw)
    assert "claude-hooks.json" not in raw


def test_mcp_registration_points_at_wrapper() -> None:
    mcp = _load_json(".mcp.json")
    server = mcp["mcpServers"]["memory-mcp"]
    assert server["command"].endswith("memory-mcp-server.sh")
    assert server["env"]["MEMORY_MCP_ROOT"] == ".memory-mcp"


def test_claude_hooks_cover_lifecycle_events() -> None:
    hooks = _load_json("hooks/claude-hooks.json")["hooks"]
    assert set(hooks) == {"UserPromptSubmit", "PostToolUse", "Stop"}

    expected_event_types = {
        "UserPromptSubmit": "user_prompt",
        "PostToolUse": "tool_result",
        "Stop": "turn_stop",
    }
    for event, entries in hooks.items():
        commands = [
            hook["command"]
            for entry in entries
            for hook in entry["hooks"]
        ]
        assert len(commands) == 1
        command = commands[0]
        assert "memory-mcp-event append" in command
        assert "--adapter claude" in command
        assert f"--event-type {expected_event_types[event]}" in command
        # Both the uv project dir and the store resolve to the plugin (repo)
        # root so the hook store matches the MCP server store. Project scoping
        # comes from the payload cwd, not a per-project store path.
        assert "${CLAUDE_PLUGIN_ROOT}" in command
        assert "${CLAUDE_PLUGIN_ROOT}/.memory-mcp" in command
        assert "${CLAUDE_PROJECT_DIR}" not in command


def test_post_tool_use_matches_all_tools() -> None:
    hooks = _load_json("hooks/claude-hooks.json")["hooks"]
    assert hooks["PostToolUse"][0]["matcher"] == "*"


@pytest.mark.parametrize(
    "relative",
    [
        "install-commands/setup-claude-plugin.sh",
        "install-commands/uninstall-claude-plugin.sh",
        "install-commands/scripts/register-claude-plugin.sh",
    ],
)
def test_claude_scripts_are_executable(relative: str) -> None:
    path = REPO_ROOT / relative
    assert path.exists()
    assert os.access(path, os.X_OK)


def test_claude_packaging_separate_from_codex() -> None:
    # The two plugin manifests stay independent so Claude packaging never
    # depends on Codex packaging.
    assert (REPO_ROOT / ".claude-plugin/plugin.json").exists()
    assert (REPO_ROOT / ".codex-plugin/plugin.json").exists()
    assert (REPO_ROOT / "hooks/claude-hooks.json").exists()
    assert (REPO_ROOT / "hooks/codex-hooks.json").exists()
