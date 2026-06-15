from __future__ import annotations

from datetime import datetime, timezone

from memory_mcp.core.events import (
    EventCreate,
    EventStore,
    MemoryCandidateCreate,
    SessionSegmentRecord,
)
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker
from memory_mcp.review.service import (
    CandidateFilters,
    CandidateReviewService,
    CandidateUpdate,
)

from conftest import FakeEmbedder


def _service(tmp_path) -> CandidateReviewService:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    memory_store = LocalMemoryStore(root, FakeEmbedder())
    return CandidateReviewService(
        event_store=event_store,
        candidate_worker=CandidateWorker(
            event_store=event_store,
            memory_store=memory_store,
        ),
    )


def test_review_service_lists_active_memories_readonly(tmp_path) -> None:
    service = _service(tmp_path)
    record = service.candidate_worker.memory_store.create_memory(
        MemoryCreate(
            what_happened="pytest used the wrong environment.",
            when_useful="When running tests in this repo.",
            helpful_explanation="Use uv run pytest.",
            tags=["testing"],
        )
    )

    memories = service.list_active_memories()
    assert [memory.id for memory in memories] == [record.id]
    assert memories[0].status == "active"

    detail = service.get_memory_detail(record.id)
    assert detail.id == record.id
    assert detail.what_happened == "pytest used the wrong environment."

    # The review service exposes no mutation path for active memories.
    assert not hasattr(service, "update_memory")


def test_review_service_get_memory_detail_missing(tmp_path) -> None:
    service = _service(tmp_path)
    try:
        service.get_memory_detail("mem_does_not_exist")
    except ValueError as exc:
        assert "memory not found" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected ValueError for missing memory")


def test_review_service_returns_candidate_with_evidence(tmp_path) -> None:
    service = _service(tmp_path)
    event = service.event_store.append_event(
        EventCreate(
            event_type="tool_result",
            source="test",
            project="/repo",
            session_id="s1",
            payload={"message": "pytest failed"},
        )
    )
    candidate = service.event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests.",
            lesson="pytest used the wrong environment.",
            action="Use uv run pytest.",
            category="testing",
            evidence_event_ids=[event.id],
            evidence_summary="The command failed in the wrong environment.",
            creation_reason="User correction.",
        )
    )

    detail = service.get_candidate_detail(candidate.id)

    assert detail.candidate.id == candidate.id
    assert [evidence.id for evidence in detail.evidence_events] == [event.id]


def test_review_service_filters_by_project_and_confidence(tmp_path) -> None:
    service = _service(tmp_path)
    segment = SessionSegmentRecord(
        id="seg_1",
        project="/repo",
        session_id="s1",
        segment_index=0,
        first_event_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        last_event_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        event_count=1,
        status="idle",
    )
    service.event_store.upsert_session_segment(segment)
    service.event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests.",
            lesson="Use the project test runner.",
            action="Use uv run pytest.",
            category="testing",
            confidence=0.9,
            evidence_summary="User correction.",
            creation_reason="User correction.",
            source_session_segment_id=segment.id,
        )
    )
    service.event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When editing docs.",
            lesson="Weak candidate.",
            action="Do something.",
            category="docs",
            confidence=0.2,
            evidence_summary="Weak signal.",
            creation_reason="Weak signal.",
        )
    )

    candidates = service.list_candidates(
        filters=CandidateFilters(project="/repo", min_confidence=0.8)
    )

    assert len(candidates) == 1
    assert candidates[0].category == "testing"


def test_review_service_edits_before_approval(tmp_path) -> None:
    service = _service(tmp_path)
    candidate = service.event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests.",
            lesson="pytest failed.",
            action="Use pytest.",
            category="testing",
            evidence_summary="Initial candidate.",
            creation_reason="Initial candidate.",
        )
    )

    updated, memory = service.approve_candidate(
        candidate.id,
        update=CandidateUpdate(
            lesson="Direct pytest used the wrong environment.",
            action="Use uv run pytest.",
            confidence=0.85,
        ),
    )

    assert updated.status == "approved"
    assert memory.what_happened == "Direct pytest used the wrong environment."
    assert memory.helpful_explanation == "Use uv run pytest."
    assert memory.confidence == 0.85


def test_review_service_retries_failed_or_skipped_segment(tmp_path) -> None:
    service = _service(tmp_path)
    segment = SessionSegmentRecord(
        id="seg_failed",
        project="/repo",
        session_id="s1",
        segment_index=0,
        first_event_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        last_event_at=datetime(2026, 6, 14, tzinfo=timezone.utc),
        event_count=1,
        status="failed",
        error="extractor failed",
    )
    service.event_store.upsert_session_segment(segment)

    retried = service.retry_segment(segment.id)

    assert retried.status == "idle"
    assert retried.error is None
