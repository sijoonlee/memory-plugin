from __future__ import annotations

from memory_mcp.models import MemoryCreate, MemoryFeedback
from memory_mcp.store import LocalMemoryStore

from conftest import FakeEmbedder


def test_create_get_search_and_export(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    record = store.create_memory(
        MemoryCreate(
            what_happened="Direct pytest used the wrong environment.",
            when_useful="When running tests in this repo.",
            helpful_explanation="Use uv run pytest so project dependencies resolve.",
            tags=["testing"],
        )
    )

    loaded = store.get_memory(record.id)
    assert loaded is not None
    assert loaded.what_happened == "Direct pytest used the wrong environment."

    results = store.search_memories("how should tests run?", tags=["testing"])
    assert [result.memory.id for result in results] == [record.id]
    assert results[0].memory.retrieval_count == 0

    loaded_after_search = store.get_memory(record.id)
    assert loaded_after_search is not None
    assert loaded_after_search.retrieval_count == 1

    output = tmp_path / "memories.jsonl"
    count = store.export_jsonl(output)
    assert count == 1
    assert record.id in output.read_text()


def test_record_feedback_updates_score_and_counters(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    record = store.create_memory(
        MemoryCreate(
            what_happened="A generated SDK was edited directly.",
            when_useful="When changing generated SDK behavior.",
            helpful_explanation="Change the source schema and regenerate.",
            tags=["sdk"],
        )
    )

    updated = store.record_feedback(
        MemoryFeedback(
            memory_id=record.id,
            signal="helpful",
            context={"reason": "The memory changed the implementation plan."},
        )
    )

    assert updated is not None
    assert updated.score == 0.75
    assert updated.positive_feedback_count == 1

    loaded = store.get_memory(record.id)
    assert loaded is not None
    assert loaded.score == 0.75
    assert loaded.positive_feedback_count == 1
