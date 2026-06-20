from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from memory_mcp.core.embeddings import LangChainHuggingFaceEmbedder
from memory_mcp.core.events import EventStore
from memory_mcp.mcp_server.service import (
    candidate_list as candidate_list_tool,
    memory_create as create_memory_tool,
    memory_delete as delete_memory_tool,
    memory_feedback as feedback_memory_tool,
    memory_get as get_memory_tool,
    memory_list as list_memory_tool,
    memory_search as search_memory_tool,
    memory_status as status_memory_tool,
)
from memory_mcp.core.store import LocalMemoryStore


def build_store(root: Path | None = None) -> LocalMemoryStore:
    store_root = root or Path(os.environ.get("MEMORY_MCP_ROOT", ".memory-mcp"))
    return LocalMemoryStore(
        root=store_root,
        embedder=LangChainHuggingFaceEmbedder(),
    )


def build_event_store(root: Path | None = None) -> EventStore:
    store_root = root or Path(os.environ.get("MEMORY_MCP_ROOT", ".memory-mcp"))
    return EventStore(root=store_root)


def build_mcp(
    store: LocalMemoryStore | None = None,
    event_store: EventStore | None = None,
) -> FastMCP:
    memory_store = store or build_store()
    events = event_store or EventStore(memory_store.root)
    mcp = FastMCP(
        "memory-mcp",
        instructions=(
            "Retrieve and manage compact reusable memories about prior work. "
            "Use memory_search when prior project context may help the current task. "
            "After memory_search, call memory_feedback only for memories you actually "
            "considered. Use signal='used' when a memory changed your plan, command, "
            "edit, or answer. Use signal='helpful' when the memory clearly improved "
            "the result or the user confirmed it. Use signal='not_helpful' when a "
            "relevant-looking memory did not help. Use signal='stale', 'incorrect', "
            "or 'contradicted' when the memory should be demoted or retired. Do not "
            "send feedback for every returned memory automatically."
        ),
    )

    @mcp.tool()
    def memory_search(
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
        min_score: float = 0.0,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve relevant memories for the current task. Pass ``project``
        (the repo path/identifier) to scope retrieval to that repo's memories
        plus global ones; omit it to search across all projects."""

        return search_memory_tool(
            memory_store,
            query=query,
            limit=limit,
            tags=tags,
            min_score=min_score,
            project=project,
            event_store=events,
            event_context={"project": project} if project is not None else None,
        )

    @mcp.tool()
    def memory_get(memory_id: str) -> dict[str, Any]:
        """Fetch one full memory by id."""

        return get_memory_tool(memory_store, memory_id=memory_id)

    @mcp.tool()
    def memory_create(
        when_useful: str,
        details: str,
        tags: list[str] | None = None,
        source: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Create one explicit memory. ``when_useful`` is the recall cue (when to
        surface it); ``details`` is the body. Pass ``project`` (the repo
        path/identifier) to scope it to that repo; omit it for a global memory
        that surfaces in every project."""

        return create_memory_tool(
            memory_store,
            when_useful=when_useful,
            details=details,
            tags=tags,
            source=source,
            project=project,
        )

    @mcp.tool()
    def memory_delete(memory_id: str) -> dict[str, Any]:
        """Permanently delete one memory by id. Hard delete: removes the memory
        and its vector, bypassing the active/stale/superseded/invalid audit
        states. Prefer memory_feedback for normal lifecycle; use delete for
        secret removal or an explicit user request to forget a memory."""

        return delete_memory_tool(memory_store, memory_id=memory_id)

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
        """Record sparse feedback for a memory that was actually considered."""

        return feedback_memory_tool(
            memory_store,
            memory_id=memory_id,
            signal=signal,
            weight=weight,
            context=context,
            event_store=events,
        )

    @mcp.tool()
    def memory_status() -> dict[str, Any]:
        """Report memory-store health: event backlog, session segments,
        candidate counts, and memory counts by status. Read-only. Call only
        when the user asks about what is stored or store health, not during
        normal task work."""

        return status_memory_tool(memory_store, events)

    @mcp.tool()
    def memory_list(status: str = "active", limit: int = 20) -> dict[str, Any]:
        """Browse stored memories by status (active, stale, superseded,
        invalid, rejected, archived). Read-only listing, distinct from the
        semantic memory_search. Call only when the user asks to see what
        memories exist."""

        return list_memory_tool(memory_store, status=status, limit=limit)

    @mcp.tool()
    def candidate_list(
        status: str = "pending_review", limit: int = 20
    ) -> dict[str, Any]:
        """List pipeline-proposed memory candidates by status (pending_review,
        rejected, merged, archived). A candidate is a pending_review memory;
        approving it activates the memory. Read-only here — approving or
        rejecting happens in the review workflow."""

        return candidate_list_tool(memory_store, status=status, limit=limit)

    return mcp


def main() -> None:
    build_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
