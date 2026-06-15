from __future__ import annotations

from memory_mcp.core.models import MemoryCreate, MemoryFeedback
from memory_mcp.core.store import LocalMemoryStore

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


def test_feedback_status_transitions(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())

    stale = _create_memory(store, "stale memory")
    contradicted_without_replacement = _create_memory(
        store,
        "contradicted memory without replacement",
    )
    contradicted_with_replacement = _create_memory(
        store,
        "contradicted memory with replacement",
    )
    incorrect = _create_memory(store, "incorrect memory")
    not_helpful = _create_memory(store, "not helpful memory")

    assert store.record_feedback(
        MemoryFeedback(memory_id=stale.id, signal="stale")
    ).status == "stale"
    assert store.record_feedback(
        MemoryFeedback(
            memory_id=contradicted_without_replacement.id,
            signal="contradicted",
        )
    ).status == "stale"
    assert store.record_feedback(
        MemoryFeedback(
            memory_id=contradicted_with_replacement.id,
            signal="contradicted",
            context={"replacement_memory_id": "mem_replacement"},
        )
    ).status == "superseded"
    assert store.record_feedback(
        MemoryFeedback(memory_id=incorrect.id, signal="incorrect")
    ).status == "invalid"
    assert store.record_feedback(
        MemoryFeedback(memory_id=not_helpful.id, signal="not_helpful")
    ).status == "active"


def test_non_active_memories_are_fetchable_but_not_searchable(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    record = _create_memory(store, "Use uv run pytest.")

    updated = store.record_feedback(
        MemoryFeedback(memory_id=record.id, signal="incorrect")
    )
    assert updated is not None
    assert updated.status == "invalid"

    assert store.get_memory(record.id).status == "invalid"
    assert store.search_memories("how should tests run?") == []


def test_duplicate_memory_create_merges_into_existing_memory(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    original = _create_memory(store, "Direct pytest used the wrong environment.")

    duplicate = store.create_memory(
        MemoryCreate(
            what_happened="Direct pytest used the wrong environment.",
            when_useful="When running tests in this repo.",
            helpful_explanation="Use uv run pytest.",
            tags=["python"],
            confidence=0.9,
            score=0.7,
        )
    )

    assert duplicate.id == original.id
    assert duplicate.tags == ["python", "testing"]
    assert duplicate.confidence == 0.9
    assert duplicate.score == 0.7
    assert duplicate.source.extra["dedupe"]["decision"] == "merged_duplicate"
    assert len(store.list_memories()) == 1


def test_possible_duplicate_memory_create_is_rejected_for_review(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    original = _create_memory(store, "Direct pytest used the wrong environment.")

    possible_duplicate = store.create_memory(
        MemoryCreate(
            what_happened="Direct pytest used a different interpreter.",
            when_useful="When running tests in this repo.",
            helpful_explanation="Use uv run pytest.",
            tags=["testing"],
        )
    )

    assert possible_duplicate.id != original.id
    assert possible_duplicate.status == "rejected"
    assert possible_duplicate.source.extra["dedupe"]["decision"] == (
        "possible_duplicate_rejected"
    )
    assert possible_duplicate.source.extra["dedupe"]["existing_memory_id"] == original.id
    assert len(store.list_memories()) == 2
    assert [result.memory.id for result in store.search_memories("run tests")] == [
        original.id
    ]


def test_distinct_memory_create_remains_active(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    first = _create_memory(store, "Direct pytest used the wrong environment.")

    second = store.create_memory(
        MemoryCreate(
            what_happened="Generated SDK files should not be edited directly.",
            when_useful="When changing generated SDK behavior.",
            helpful_explanation="Update the OpenAPI source and regenerate the SDK.",
            tags=["sdk"],
        )
    )

    assert second.id != first.id
    assert second.status == "active"
    assert len(store.list_memories(status="active")) == 2


def _create_memory(store: LocalMemoryStore, lesson: str):
    return store.create_memory(
        MemoryCreate(
            what_happened=lesson,
            when_useful="When running tests in this repo.",
            helpful_explanation="Use uv run pytest.",
            tags=["testing"],
        )
    )
