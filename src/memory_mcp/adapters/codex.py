from __future__ import annotations

from typing import Any

from memory_mcp.adapters.base import BaseAdapter, _first_str


class CodexAdapter(BaseAdapter):
    """Map Codex CLI hook lifecycle payloads into normalized events.

    Codex hook payloads expose the working directory and a session/thread
    identifier. Per-turn identifiers are used when present so multiple turns in
    one session stay linked but distinguishable.
    """

    source = "codex_hook"

    def extract_project(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "cwd", "workspace_root", "project")

    def extract_session_id(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "session_id", "thread_id", "conversation_id")

    def extract_run_id(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "turn_id", "run_id")
