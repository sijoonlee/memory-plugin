from __future__ import annotations

from datetime import datetime, timezone

from memory_mcp.core.events import (
    EventCreate,
    EventStore,
    SessionSegmentRecord,
)
import pytest
from memory_mcp.core.models import MemoryCreate, MemorySource
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


def _pending(
    service: CandidateReviewService,
    *,
    when_useful: str,
    details: str,
    memory_type: str,
    confidence: float = 0.7,
    evidence: list[str] | None = None,
    project: str | None = None,
    segment_id: str | None = None,
):
    return service.candidate_worker.create_candidate(
        MemoryCreate(
            when_useful=when_useful,
            details=details,
            memory_type=memory_type,
            confidence=confidence,
            project=project,
            source=MemorySource(
                kind="pipeline_candidate",
                evidence_event_ids=evidence or [],
                creation_reason="User correction.",
                extra={
                    "evidence_summary": "A test-command correction.",
                    **({"source_session_segment_id": segment_id} if segment_id else {}),
                },
            ),
        )
    )


def test_review_service_lists_active_memories_readonly(tmp_path) -> None:
    service = _service(tmp_path)
    record = service.candidate_worker.memory_store.create_memory(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details="pytest used the wrong environment. Use uv run pytest.",
            tags=["testing"],
        )
    )

    memories = service.list_active_memories()
    assert [memory.id for memory in memories] == [record.id]
    assert memories[0].status == "active"

    detail = service.get_memory_detail(record.id)
    assert detail.id == record.id
    assert detail.details.startswith("pytest used the wrong environment.")

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
    candidate = _pending(
        service,
        when_useful="When running tests.",
        details="pytest used the wrong environment. Use uv run pytest.",
        memory_type="feedback",
        evidence=[event.id],
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
    _pending(
        service,
        when_useful="When running tests.",
        details="Use the project test runner. Use uv run pytest.",
        memory_type="feedback",
        confidence=0.9,
        project="/repo",
        segment_id=segment.id,
    )
    _pending(
        service,
        when_useful="When editing docs.",
        details="Weak candidate. Do something.",
        memory_type="reference",
        confidence=0.2,
    )

    candidates = service.list_candidates(
        filters=CandidateFilters(project="/repo", min_confidence=0.8)
    )

    assert len(candidates) == 1
    assert candidates[0].memory_type == "feedback"


def test_review_service_edits_before_approval(tmp_path) -> None:
    service = _service(tmp_path)
    candidate = _pending(
        service,
        when_useful="When running tests.",
        details="pytest failed. Use pytest.",
        memory_type="feedback",
    )

    memory = service.approve_candidate(
        candidate.id,
        update=CandidateUpdate(
            when_useful="When running tests.",
            details="Direct pytest used the wrong environment. Use uv run pytest.",
            confidence=0.85,
        ),
    )

    assert memory.status == "active"
    assert memory.details == "Direct pytest used the wrong environment. Use uv run pytest."
    assert memory.confidence == 0.85


def _seed_segment(
    service: CandidateReviewService,
    *,
    segment_id: str,
    status: str,
    error: str | None,
    session_id: str = "s1",
) -> SessionSegmentRecord:
    segment = SessionSegmentRecord(
        id=segment_id,
        project="/repo",
        session_id=session_id,
        segment_index=0,
        first_event_at=datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc),
        last_event_at=datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc),
        event_count=1,
        status=status,
        error=error,
    )
    service.event_store.upsert_session_segment(segment)
    return segment


def test_review_service_lists_segments_with_reasons(tmp_path) -> None:
    service = _service(tmp_path)
    _seed_segment(
        service,
        segment_id="seg_skipped",
        status="skipped",
        error="No durable memory candidate found.",
    )
    _seed_segment(
        service,
        segment_id="seg_failed",
        status="failed",
        error="extractor crashed",
        session_id="s2",
    )

    all_segments = service.list_segments()
    assert {segment.id for segment in all_segments} == {"seg_skipped", "seg_failed"}

    skipped = service.list_segments(status="skipped")
    assert [segment.id for segment in skipped] == ["seg_skipped"]
    assert skipped[0].error == "No durable memory candidate found."

    assert service.list_segments(status="skipped", limit=0) == []


def test_review_service_returns_segment_event_log(tmp_path) -> None:
    service = _service(tmp_path)
    segment = _seed_segment(
        service,
        segment_id="seg_skipped",
        status="skipped",
        error="No durable memory candidate found.",
    )
    service.event_store.append_event(
        EventCreate(
            event_type="tool_result",
            source="test",
            project="/repo",
            session_id="s1",
            payload={"message": "ran pytest"},
        ),
        created_at=datetime(2026, 6, 14, 0, 30, tzinfo=timezone.utc),
    )

    detail = service.get_segment_detail(segment.id)

    assert detail.segment.id == segment.id
    assert [event.payload["message"] for event in detail.events] == ["ran pytest"]


def test_review_service_segment_detail_missing(tmp_path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="session segment not found"):
        service.get_segment_detail("seg_does_not_exist")


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
