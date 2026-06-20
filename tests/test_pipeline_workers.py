from __future__ import annotations

from datetime import date, timedelta

from memory_mcp.core.events import EventCreate, EventStore
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.workers.decay_worker import DAILY_DECAY_CHECKPOINT, DecayWorker
from memory_mcp.pipeline.workers.event_worker import EventWorker

from conftest import FakeEmbedder


def test_event_worker_processes_feedback_events_and_marks_processed(tmp_path) -> None:
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    event_store = EventStore(tmp_path / "memory")
    worker = EventWorker(memory_store=memory_store, event_store=event_store)
    memory = memory_store.create_memory(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details="Direct pytest used the wrong environment. Use uv run pytest.",
            tags=["testing"],
        )
    )
    event_store.append_event(
        EventCreate(
            event_type="memory_feedback",
            source="test",
            payload={
                "memory_id": memory.id,
                "signal": "helpful",
                "weight": 1.0,
                "context": {"reason": "it helped"},
            },
        )
    )

    result = worker.run_once()

    assert result.processed == 1
    assert result.failed == 0
    assert result.remaining == 0
    assert event_store.count_unprocessed() == 0
    updated = memory_store.get_memory(memory.id)
    assert updated is not None
    assert updated.score == 0.75
    assert updated.positive_feedback_count == 1


def test_event_worker_feedback_event_can_mark_memory_invalid(tmp_path) -> None:
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    event_store = EventStore(tmp_path / "memory")
    worker = EventWorker(memory_store=memory_store, event_store=event_store)
    memory = memory_store.create_memory(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details="Use direct pytest. Run pytest directly.",
            tags=["testing"],
        )
    )
    event_store.append_event(
        EventCreate(
            event_type="memory_feedback",
            source="test",
            payload={
                "memory_id": memory.id,
                "signal": "incorrect",
                "context": {"reason": "The repo uses uv."},
            },
        )
    )

    result = worker.run_once()

    assert result.processed == 1
    updated = memory_store.get_memory(memory.id)
    assert updated is not None
    assert updated.status == "invalid"


def test_event_worker_skips_already_applied_feedback_events(tmp_path) -> None:
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    event_store = EventStore(tmp_path / "memory")
    worker = EventWorker(memory_store=memory_store, event_store=event_store)
    memory = memory_store.create_memory(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details="Direct pytest used the wrong environment. Use uv run pytest.",
            tags=["testing"],
        )
    )
    event_store.append_event(
        EventCreate(
            event_type="memory_feedback",
            source="mcp_tool",
            payload={
                "memory_id": memory.id,
                "signal": "helpful",
                "already_applied": True,
            },
        )
    )

    result = worker.run_once()

    assert result.processed == 1
    updated = memory_store.get_memory(memory.id)
    assert updated is not None
    assert updated.score == 0.5
    assert updated.positive_feedback_count == 0


def test_event_worker_processes_retrieval_event_as_weak_score_signal(tmp_path) -> None:
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    event_store = EventStore(tmp_path / "memory")
    worker = EventWorker(memory_store=memory_store, event_store=event_store)
    memory = memory_store.create_memory(
        MemoryCreate(
            when_useful="When running tests.",
            details="Use uv run pytest. Direct pytest can use the wrong environment.",
            tags=["testing"],
        )
    )
    event_store.append_event(
        EventCreate(
            event_type="memory_retrieved",
            source="test",
            payload={"memory_ids": [memory.id], "query": "run tests"},
        )
    )

    result = worker.run_once()

    assert result.processed == 1
    updated = memory_store.get_memory(memory.id)
    assert updated is not None
    assert updated.score == 0.51
    assert updated.retrieval_count == 0


def test_event_worker_marks_invalid_event_failed(tmp_path) -> None:
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    event_store = EventStore(tmp_path / "memory")
    worker = EventWorker(memory_store=memory_store, event_store=event_store)
    event_store.append_event(
        EventCreate(
            event_type="memory_feedback",
            source="test",
            payload={"signal": "helpful"},
        )
    )

    result = worker.run_once()

    assert result.processed == 0
    assert result.failed == 1
    assert event_store.count_failed() == 1


def test_decay_worker_applies_daily_decay_once_per_day(tmp_path) -> None:
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    worker = DecayWorker(memory_store=memory_store)
    memory = memory_store.create_memory(
        MemoryCreate(
            when_useful="When running tests.",
            details="Use uv run pytest. Direct pytest can use the wrong environment.",
            tags=["testing"],
            score=1.0,
        )
    )
    today = date(2026, 6, 14)
    memory_store.set_checkpoint(
        DAILY_DECAY_CHECKPOINT,
        (today - timedelta(days=2)).isoformat(),
    )

    decayed = worker.run_once(today=today)
    second_decay = worker.run_once(today=today)

    assert decayed == 1
    assert second_decay == 0
    updated = memory_store.get_memory(memory.id)
    assert updated is not None
    assert round(updated.score, 6) == round(0.995**2, 6)
