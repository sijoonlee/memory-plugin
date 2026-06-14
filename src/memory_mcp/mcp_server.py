from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from memory_mcp.embeddings import LangChainHuggingFaceEmbedder
from memory_mcp.service import (
    memory_create as create_memory_tool,
    memory_feedback as feedback_memory_tool,
    memory_get as get_memory_tool,
    memory_search as search_memory_tool,
)
from memory_mcp.store import LocalMemoryStore


def build_store(root: Path | None = None) -> LocalMemoryStore:
    store_root = root or Path(os.environ.get("MEMORY_MCP_ROOT", ".memory-mcp"))
    return LocalMemoryStore(
        root=store_root,
        embedder=LangChainHuggingFaceEmbedder(),
    )


def build_mcp(store: LocalMemoryStore | None = None) -> FastMCP:
    memory_store = store or build_store()
    mcp = FastMCP(
        "memory-mcp",
        instructions=(
            "Retrieve and manage compact reusable memories about prior work."
        ),
    )

    @mcp.tool()
    def memory_search(
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """Retrieve relevant memories for the current task."""

        return search_memory_tool(
            memory_store,
            query=query,
            limit=limit,
            tags=tags,
            min_score=min_score,
        )

    @mcp.tool()
    def memory_get(memory_id: str) -> dict[str, Any]:
        """Fetch one full memory by id."""

        return get_memory_tool(memory_store, memory_id=memory_id)

    @mcp.tool()
    def memory_create(
        what_happened: str,
        when_useful: str,
        helpful_explanation: str,
        tags: list[str] | None = None,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one explicit memory."""

        return create_memory_tool(
            memory_store,
            what_happened=what_happened,
            when_useful=when_useful,
            helpful_explanation=helpful_explanation,
            tags=tags,
            source=source,
        )

    @mcp.tool()
    def memory_feedback(
        memory_id: str,
        signal: Literal[
            "retrieved",
            "used",
            "helpful",
            "not_helpful",
            "incorrect",
            "stale",
            "contradicted",
        ],
        weight: float = 1.0,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record feedback about whether a memory was used or helpful."""

        return feedback_memory_tool(
            memory_store,
            memory_id=memory_id,
            signal=signal,
            weight=weight,
            context=context,
        )

    return mcp


def main() -> None:
    build_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
