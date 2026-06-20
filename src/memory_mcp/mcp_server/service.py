from __future__ import annotations

from typing import Any

from memory_mcp.core.events import EventCreate, EventStore
from memory_mcp.core.models import (
    MEMORY_TYPES,
    MemoryCreate,
    MemoryFeedback,
    MemoryRecord,
    MemorySource,
)
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.operator import OperatorWorkflow

_MEMORY_SUMMARY_FIELDS = (
    "id",
    "when_useful",
    "details",
    "memory_type",
    "tags",
    "project",
    "status",
    "score",
    "confidence",
    "created_at",
    "updated_at",
)


def memory_search(
    store: LocalMemoryStore,
    query: str,
    limit: int = 5,
    tags: list[str] | None = None,
    min_score: float = 0.0,
    project: str | None = None,
    event_store: EventStore | None = None,
    event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Default the retrieval scope to the caller's project so search is
    # repo-scoped (inclusive of global memories) unless overridden.
    if project is None:
        project = _context_value(event_context, "project")
    results = store.search_memories(
        query,
        limit=limit,
        tags=tags,
        min_score=min_score,
        project=project,
    )
    response = {
        "memories": [
            {
                "id": result.memory.id,
                "when_useful": result.memory.when_useful,
                "details": result.memory.details,
                "memory_type": result.memory.memory_type,
                "tags": result.memory.tags,
                "score": result.memory.score,
                "confidence": result.memory.confidence,
                "semantic_similarity": result.semantic_similarity,
                "final_score": result.final_score,
                "retrieval_reason": result.retrieval_reason,
            }
            for result in results
        ],
        "feedback_guidance": (
            "Call memory_feedback only for memories you actually considered. "
            "Use signal='used' if a memory changed your behavior. Use 'helpful' "
            "if it clearly improved the result or the user confirmed it. Use "
            "'not_helpful' if it looked relevant but did not help. Use 'stale', "
            "'incorrect', or 'contradicted' when the memory should be demoted or retired. "
            "Do not send feedback for every returned memory automatically."
        ),
    }
    if event_store is not None:
        _append_event(
            event_store,
            EventCreate(
                event_type="memory_retrieved",
                source="mcp_tool",
                project=_context_value(event_context, "project"),
                session_id=_context_value(event_context, "session_id"),
                run_id=_context_value(event_context, "run_id"),
                payload={
                    "query": query,
                    "limit": limit,
                    "tags": tags or [],
                    "min_score": min_score,
                    "memory_ids": [item["id"] for item in response["memories"]],
                    "result_count": len(response["memories"]),
                },
            ),
        )
    return response


def memory_get(store: LocalMemoryStore, memory_id: str) -> dict[str, Any]:
    record = store.get_memory(memory_id)
    if record is None:
        return {"memory": None}
    return {"memory": record.model_dump(mode="json")}


def memory_create(
    store: LocalMemoryStore,
    when_useful: str,
    details: str,
    memory_type: str,
    tags: list[str] | None = None,
    source: dict[str, Any] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    # ``memory_type`` is mandatory for manual creation: an untyped memory is not
    # a valid persisted state, so we reject it at the entry point rather than
    # saving it and sweeping later.
    if memory_type not in MEMORY_TYPES:
        raise ValueError(
            f"memory_type must be one of {list(MEMORY_TYPES)}, got {memory_type!r}"
        )
    record = store.create_memory(
        MemoryCreate(
            when_useful=when_useful,
            details=details,
            memory_type=memory_type,  # type: ignore[arg-type]
            tags=tags or [],
            source=MemorySource.model_validate(source or {"kind": "manual"}),
            project=project,
        )
    )
    return {"memory": record.model_dump(mode="json")}


def memory_delete(store: LocalMemoryStore, memory_id: str) -> dict[str, Any]:
    deleted = store.delete_memory(memory_id)
    return {"deleted": deleted, "memory_id": memory_id}


def memory_feedback(
    store: LocalMemoryStore,
    memory_id: str,
    signal: str,
    weight: float = 1.0,
    context: dict[str, Any] | None = None,
    event_store: EventStore | None = None,
) -> dict[str, Any]:
    updated = store.record_feedback(
        MemoryFeedback(
            memory_id=memory_id,
            signal=signal,  # type: ignore[arg-type]
            weight=weight,
            context=context or {},
        )
    )
    if updated is None:
        response = {"ok": False, "memory": None, "error": "memory_not_found"}
    else:
        response = {"ok": True, "memory": updated.model_dump(mode="json")}
    if event_store is not None:
        _append_event(
            event_store,
            EventCreate(
                event_type="memory_feedback",
                source="mcp_tool",
                project=_context_value(context, "project"),
                session_id=_context_value(context, "session_id"),
                run_id=_context_value(context, "run_id"),
                payload={
                    "memory_id": memory_id,
                    "signal": signal,
                    "weight": weight,
                    "context": context or {},
                    "ok": response["ok"],
                    "error": response.get("error"),
                    "already_applied": response["ok"],
                },
            ),
        )
    return response


def memory_status(
    store: LocalMemoryStore,
    event_store: EventStore,
) -> dict[str, Any]:
    """Aggregate counts for events, sessions, candidates, and memories."""

    workflow = OperatorWorkflow(
        root=store.root,
        memory_store=store,
        event_store=event_store,
    )
    return workflow.status().to_dict()


def memory_list(
    store: LocalMemoryStore,
    status: str = "active",
    limit: int = 20,
) -> dict[str, Any]:
    """List stored memories of a given status (browse, not semantic search)."""

    records = store.list_memories(status=status)
    return {
        "status": status,
        "total": len(records),
        "returned": min(len(records), max(limit, 0)),
        "memories": [_memory_summary(record) for record in records[:limit]],
    }


def candidate_list(
    store: LocalMemoryStore,
    status: str = "pending_review",
    limit: int = 20,
) -> dict[str, Any]:
    """List pipeline-proposed memory candidates (pending_review memories)."""

    records = store.list_memories(status=status)
    return {
        "status": status,
        "total": len(records),
        "returned": min(len(records), max(limit, 0)),
        "candidates": [record.model_dump(mode="json") for record in records[:limit]],
    }


def _memory_summary(record: MemoryRecord) -> dict[str, Any]:
    dumped = record.model_dump(mode="json")
    return {field: dumped[field] for field in _MEMORY_SUMMARY_FIELDS}


def _context_value(context: dict[str, Any] | None, key: str) -> str | None:
    if context is None:
        return None
    value = context.get(key)
    if value is None:
        return None
    return str(value)


def _append_event(event_store: EventStore, event: EventCreate) -> None:
    event_store.append_event(event)
