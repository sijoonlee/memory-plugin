from __future__ import annotations

from memory_mcp.service import (
    memory_create,
    memory_feedback,
    memory_get,
    memory_search,
)
from memory_mcp.store import LocalMemoryStore

from conftest import FakeEmbedder


def test_mcp_service_contracts(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())

    created = memory_create(
        store,
        what_happened="Direct pytest used the wrong environment.",
        when_useful="When running tests in this repo.",
        helpful_explanation="Use uv run pytest.",
        tags=["testing"],
        source={"kind": "manual"},
    )

    memory_id = created["memory"]["id"]
    assert created["memory"]["what_happened"] == "Direct pytest used the wrong environment."

    found = memory_search(
        store,
        query="how should tests run?",
        tags=["testing"],
    )
    assert found["memories"][0]["id"] == memory_id
    assert "retrieval_reason" in found["memories"][0]

    fetched = memory_get(store, memory_id)
    assert fetched["memory"]["id"] == memory_id

    feedback = memory_feedback(
        store,
        memory_id=memory_id,
        signal="used",
        context={"reason": "The agent followed it."},
    )
    assert feedback["ok"] is True
    assert feedback["memory"]["use_count"] == 1
