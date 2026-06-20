from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory_mcp.core.events import EventCreate, EventStore
from memory_mcp.core.models import MemoryCreate, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker
from memory_mcp.pipeline.workers.session_worker import SessionWorker

from conftest import FakeEmbedder


def test_session_worker_splits_segments_and_marks_idle(tmp_path) -> None:
    store = EventStore(tmp_path / "memory")
    start = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    for offset in (0, 60, 3 * 60 * 60):
        store.append_event(
            EventCreate(
                event_type="user_prompt",
                source="test",
                project="/repo",
                session_id="session-1",
                payload={"offset": offset},
            ),
            created_at=start + timedelta(seconds=offset),
        )

    result = SessionWorker(
        event_store=store,
        idle_after_seconds=600,
        max_segment_gap_seconds=7200,
    ).run_once(now=start + timedelta(hours=3, minutes=20))

    assert result.scanned_events == 3
    assert result.upserted_segments == 2
    assert result.idle_segments == 2
    segments = store.list_session_segments()
    assert [segment.event_count for segment in segments] == [2, 1]
    assert {segment.status for segment in segments} == {"idle"}


def test_candidate_worker_approves_candidate_into_memory(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    worker = CandidateWorker(event_store=event_store, memory_store=memory_store)
    candidate = worker.create_candidate(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details="Direct pytest uses the wrong environment. Use uv run pytest.",
            tags=["durable_workflow"],
            confidence=0.8,
            source=MemorySource(
                kind="pipeline_candidate",
                evidence_event_ids=["evt_1"],
                creation_reason="User correction in session.",
                extra={
                    "evidence_summary": "The user corrected the test command.",
                },
            ),
        )
    )

    assert candidate.status == "pending_review"
    memory = worker.approve_candidate(candidate.id)

    # The candidate and the memory are the same record; status flips to active.
    assert memory.id == candidate.id
    assert memory.status == "active"
    assert memory_store.get_memory(memory.id).status == "active"
    assert memory.when_useful == "When running tests in this repo."
    assert memory.details.startswith("Direct pytest uses the wrong environment.")
    assert memory.source.kind == "pipeline_candidate"
    assert memory.source.evidence_event_ids == ["evt_1"]


def test_candidate_worker_rejects_and_retries_candidate(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    worker = CandidateWorker(event_store=event_store, memory_store=memory_store)
    candidate = worker.create_candidate(
        MemoryCreate(
            when_useful="When using local docs.",
            details="A vague lesson. Do something.",
            tags=["user_correction"],
            source=MemorySource(
                kind="pipeline_candidate",
                creation_reason="Weak extractor output.",
                extra={"evidence_summary": "No concrete evidence."},
            ),
        )
    )

    rejected = worker.reject_candidate(candidate.id, reason="Too vague.")
    retried = worker.retry_candidate(candidate.id)

    assert rejected.status == "rejected"
    assert rejected.source.extra["rejection_reason"] == "Too vague."
    assert retried.status == "pending_review"
    assert "rejection_reason" not in retried.source.extra
