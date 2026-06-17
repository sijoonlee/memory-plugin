from __future__ import annotations

from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.mcp_server.service import memory_delete

from conftest import FakeEmbedder


def _create(store: LocalMemoryStore, lesson: str, tag: str) -> str:
    record = store.create_memory(
        MemoryCreate(
            what_happened=lesson,
            when_useful=f"When working on {tag}.",
            helpful_explanation=f"Do the {tag} thing.",
            tags=[tag],
        )
    )
    return record.id


def test_delete_removes_from_metadata_and_vector_store(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    memory_id = _create(store, "Use uv run pytest.", "testing")

    assert store.delete_memory(memory_id) is True

    # Gone from the metadata store.
    assert store.get_memory(memory_id) is None
    # Gone from the vector store, so it cannot be retrieved by search.
    results = store.search_memories("how should I run tests?", tags=["testing"])
    assert [result.memory.id for result in results] == []


def test_delete_unknown_id_is_safe_no_op(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    assert store.delete_memory("mem_does_not_exist") is False


def test_delete_one_memory_does_not_affect_others(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    keep_id = _create(store, "Keep this lesson about linting.", "linting")
    drop_id = _create(store, "Drop this lesson about testing.", "testing")

    assert store.delete_memory(drop_id) is True

    assert store.get_memory(keep_id) is not None
    assert store.get_memory(drop_id) is None
    results = store.search_memories("linting guidance", tags=["linting"])
    assert [result.memory.id for result in results] == [keep_id]


def test_service_memory_delete_reports_outcome(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    memory_id = _create(store, "Use uv run pytest.", "testing")

    assert memory_delete(store, memory_id) == {
        "deleted": True,
        "memory_id": memory_id,
    }
    assert memory_delete(store, memory_id) == {
        "deleted": False,
        "memory_id": memory_id,
    }
