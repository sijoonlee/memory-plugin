from __future__ import annotations

from typing import Any

from memory_mcp.models import MemoryCreate, MemoryFeedback, MemorySource
from memory_mcp.store import LocalMemoryStore


def memory_search(
    store: LocalMemoryStore,
    query: str,
    limit: int = 5,
    tags: list[str] | None = None,
    min_score: float = 0.0,
) -> dict[str, Any]:
    results = store.search_memories(
        query,
        limit=limit,
        tags=tags,
        min_score=min_score,
    )
    return {
        "memories": [
            {
                "id": result.memory.id,
                "what_happened": result.memory.what_happened,
                "when_useful": result.memory.when_useful,
                "helpful_explanation": result.memory.helpful_explanation,
                "tags": result.memory.tags,
                "score": result.memory.score,
                "confidence": result.memory.confidence,
                "semantic_similarity": result.semantic_similarity,
                "final_score": result.final_score,
                "retrieval_reason": result.retrieval_reason,
            }
            for result in results
        ]
    }


def memory_get(store: LocalMemoryStore, memory_id: str) -> dict[str, Any]:
    record = store.get_memory(memory_id)
    if record is None:
        return {"memory": None}
    return {"memory": record.model_dump(mode="json")}


def memory_create(
    store: LocalMemoryStore,
    what_happened: str,
    when_useful: str,
    helpful_explanation: str,
    tags: list[str] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = store.create_memory(
        MemoryCreate(
            what_happened=what_happened,
            when_useful=when_useful,
            helpful_explanation=helpful_explanation,
            tags=tags or [],
            source=MemorySource.model_validate(source or {"kind": "manual"}),
        )
    )
    return {"memory": record.model_dump(mode="json")}


def memory_feedback(
    store: LocalMemoryStore,
    memory_id: str,
    signal: str,
    weight: float = 1.0,
    context: dict[str, Any] | None = None,
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
        return {"ok": False, "memory": None, "error": "memory_not_found"}
    return {"ok": True, "memory": updated.model_dump(mode="json")}
