from __future__ import annotations

from typing import Any

from memory_mcp.adapters.base import BaseAdapter, _first_str


class ClaudeAdapter(BaseAdapter):
    """Map Claude Code hook lifecycle payloads into normalized events.

    Claude Code hooks deliver a JSON object on stdin that includes ``cwd`` and
    ``session_id`` for every lifecycle event (``UserPromptSubmit``,
    ``PostToolUse``, ``Stop``, ...). Claude Code has no separate per-turn id, so
    ``run_id`` is left unset and the session id carries grouping. When
    ``session_id`` is absent the base adapter applies a project-scoped fallback.
    """

    source = "claude_hook"

    def extract_project(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "cwd", "project_dir", "workspace_root")

    def extract_session_id(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "session_id")

    def extract_run_id(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "run_id")
