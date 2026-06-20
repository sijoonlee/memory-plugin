from __future__ import annotations

from memory_mcp.mcp_server.service import (
    memory_create,
    memory_feedback,
    memory_get,
    memory_search,
)
from memory_mcp.core.events import EventStore
from memory_mcp.core.store import LocalMemoryStore

from conftest import FakeEmbedder


def test_mcp_service_contracts(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    events = EventStore(tmp_path / "memory")

    created = memory_create(
        store,
        when_useful="When running tests in this repo.",
        details="Direct pytest used the wrong environment. Use uv run pytest.",
        tags=["testing"],
        source={"kind": "manual"},
    )

    memory_id = created["memory"]["id"]
    assert created["memory"]["details"] == (
        "Direct pytest used the wrong environment. Use uv run pytest."
    )

    found = memory_search(
        store,
        query="how should tests run?",
        tags=["testing"],
        event_store=events,
        event_context={"project": "/repo", "session_id": "session-1"},
    )
    assert found["memories"][0]["id"] == memory_id
    assert "retrieval_reason" in found["memories"][0]
    assert "feedback_guidance" in found
    assert "actually considered" in found["feedback_guidance"]

    fetched = memory_get(store, memory_id)
    assert fetched["memory"]["id"] == memory_id

    feedback = memory_feedback(
        store,
        memory_id=memory_id,
        signal="used",
        context={"reason": "The agent followed it."},
        event_store=events,
    )
    assert feedback["ok"] is True
    assert feedback["memory"]["use_count"] == 1

    pending_events = events.list_unprocessed()
    assert [event.event_type for event in pending_events] == [
        "memory_retrieved",
        "memory_feedback",
    ]
    assert pending_events[0].payload["memory_ids"] == [memory_id]
    assert pending_events[1].payload["memory_id"] == memory_id
    assert pending_events[1].payload["already_applied"] is True
