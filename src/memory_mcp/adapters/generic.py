from __future__ import annotations

from typing import Any

from memory_mcp.adapters.base import BaseAdapter, _first_str


class GenericAdapter(BaseAdapter):
    """Pass-through adapter for any MCP client or custom integration.

    The source is supplied by the caller (the ``--source`` ingestion flag) so
    arbitrary runtimes can write normalized events without a dedicated adapter.
    Common identifier keys are still read from the payload as a convenience.
    """

    def __init__(self, source: str) -> None:
        self.source = source

    def extract_project(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "cwd", "project", "workspace_root")

    def extract_session_id(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "session_id", "thread_id")

    def extract_run_id(self, payload: dict[str, Any]) -> str | None:
        return _first_str(payload, "run_id", "turn_id")
