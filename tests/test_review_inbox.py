from __future__ import annotations

import pytest

from memory_mcp.core.events import EventStore
from memory_mcp.core.models import MemoryCreate, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker
from memory_mcp.review.service import CandidateReviewService

from conftest import FakeEmbedder


def _store(tmp_path) -> LocalMemoryStore:
    return LocalMemoryStore(tmp_path / "memory", FakeEmbedder())


def _mk(store, when_useful, details, tag, kind="manual"):
    return store.create_memory(
        MemoryCreate(
            when_useful=when_useful,
            details=details,
            tags=[tag],
            source=MemorySource(kind=kind),
        )
    )


# ---- store-level (is_reviewed + archive/restore) ----


def test_created_memory_starts_unread(tmp_path) -> None:
    store = _store(tmp_path)
    m = _mk(store, "When testing things.", "A sample lesson about coverage.", "t")
    assert m.is_reviewed is False
    assert store.get_memory(m.id).is_reviewed is False


def test_set_reviewed_toggles_without_affecting_retrieval(tmp_path) -> None:
    store = _store(tmp_path)
    m = _mk(store, "When configuring CI.", "Cache the uv directory between runs.", "ci")

    assert store.set_reviewed(m.id, True).is_reviewed is True
    # read/unread does not gate retrieval
    assert [r.memory.id for r in store.search_memories("CI cache directory")] == [m.id]
    assert store.set_reviewed(m.id, False).is_reviewed is False


def test_list_memories_filters_by_is_reviewed(tmp_path) -> None:
    store = _store(tmp_path)
    a = _mk(store, "When naming columns.", "Use snake_case for database columns.", "db")
    b = _mk(store, "When styling charts.", "Use the shared theme palette.", "ui")
    store.set_reviewed(a.id, True)

    assert [m.id for m in store.list_memories(status="active", is_reviewed=False)] == [b.id]
    assert [m.id for m in store.list_memories(status="active", is_reviewed=True)] == [a.id]


def test_archive_then_restore_round_trips_without_reembedding(tmp_path) -> None:
    store = _store(tmp_path)
    m = _mk(store, "When deploying billing.", "Rotate staging creds before each release.", "dep")

    assert store.archive_memory(m.id).status == "archived"
    # excluded from search and the active listing, kept under archived
    assert store.search_memories("deploy billing creds") == []
    assert store.list_memories(status="active") == []
    assert [x.id for x in store.list_memories(status="archived")] == [m.id]

    # restore brings it back, searchable again (vector persisted — no re-embed)
    assert store.restore_memory(m.id).status == "active"
    assert [r.memory.id for r in store.search_memories("deploy billing creds")] == [m.id]


def test_archive_and_set_reviewed_unknown_id_is_none(tmp_path) -> None:
    store = _store(tmp_path)
    assert store.archive_memory("mem_nope") is None
    assert store.restore_memory("mem_nope") is None
    assert store.set_reviewed("mem_nope", True) is None


# ---- review service (memory manager) ----


def _service(tmp_path):
    root = tmp_path / "memory"
    event_store = EventStore(root)
    memory_store = LocalMemoryStore(root, FakeEmbedder())
    service = CandidateReviewService(
        event_store=event_store,
        candidate_worker=CandidateWorker(
            event_store=event_store,
            memory_store=memory_store,
        ),
    )
    return service, memory_store


def test_service_unread_inbox_and_manual_filter(tmp_path) -> None:
    service, store = _service(tmp_path)
    auto = store.create_memory(
        MemoryCreate(
            when_useful="When running tests.",
            details="Use uv run pytest.",
            tags=["t"],
            source=MemorySource(kind="pipeline_candidate"),
        )
    )
    manual = store.create_memory(
        MemoryCreate(
            when_useful="When deploying the service.",
            details="Rotate credentials first.",
            tags=["d"],
            source=MemorySource(kind="manual"),
        )
    )

    inbox = service.list_memories(status="active", is_reviewed=False)
    assert {m.id for m in inbox} == {auto.id, manual.id}

    assert [m.id for m in service.list_memories(status="active", manual=True)] == [manual.id]
    assert [m.id for m in service.list_memories(status="active", manual=False)] == [auto.id]


def test_service_set_reviewed_archive_restore_delete(tmp_path) -> None:
    service, store = _service(tmp_path)
    m = store.create_memory(
        MemoryCreate(
            when_useful="When configuring CI.",
            details="Cache the uv directory.",
            tags=["ci"],
            source=MemorySource(kind="manual"),
        )
    )

    assert service.set_reviewed(m.id, True).is_reviewed is True

    service.archive_memory(m.id)
    assert service.list_memories(status="active") == []
    assert [x.id for x in service.list_memories(status="archived")] == [m.id]

    assert service.restore_memory(m.id).status == "active"
    assert service.delete_memory(m.id) is True
    assert service.list_memories(status="active") == []


def test_service_missing_id_raises(tmp_path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(ValueError, match="memory not found"):
        service.set_reviewed("mem_nope", True)
    with pytest.raises(ValueError, match="memory not found"):
        service.archive_memory("mem_nope")
