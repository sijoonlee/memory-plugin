from __future__ import annotations

from memory_mcp.adapters.base import BaseAdapter, fallback_session_id
from memory_mcp.adapters.claude import ClaudeAdapter
from memory_mcp.adapters.codex import CodexAdapter
from memory_mcp.adapters.generic import GenericAdapter

ADAPTER_NAMES = ("codex", "claude", "generic")


def get_adapter(name: str, *, source: str | None = None) -> BaseAdapter:
    """Resolve an adapter by name.

    ``codex`` and ``claude`` carry their own source identifier. ``generic``
    requires an explicit ``source`` so the ingestion boundary stays usable for
    arbitrary MCP clients.
    """

    if name == "codex":
        return CodexAdapter()
    if name == "claude":
        return ClaudeAdapter()
    if name == "generic":
        if not source:
            raise ValueError("generic adapter requires a source")
        return GenericAdapter(source)
    raise ValueError(f"unknown adapter '{name}'; expected one of {ADAPTER_NAMES}")


__all__ = [
    "ADAPTER_NAMES",
    "BaseAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "GenericAdapter",
    "fallback_session_id",
    "get_adapter",
]
