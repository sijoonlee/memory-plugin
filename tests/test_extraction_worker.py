from __future__ import annotations

from datetime import datetime, timezone

from memory_mcp.core.events import EventCreate, EventStore
from memory_mcp.daemon.extractors import (
    ExtractedMemoryCandidate,
    ExtractionResult,
    StaticMemoryExtractor,
)
from memory_mcp.daemon.workers.extraction_worker import ExtractionWorker
from memory_mcp.daemon.workers.session_worker import SessionWorker


def test_extraction_schema_forbids_additional_properties() -> None:
    schema = ExtractionResult.model_json_schema()

    assert schema["additionalProperties"] is False
    candidate_schema = schema["$defs"]["ExtractedMemoryCandidate"]
    assert candidate_schema["additionalProperties"] is False


def test_extraction_worker_creates_pending_candidate_from_idle_segment(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"prompt": "Use uv run pytest in this repo."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc)
    )
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(
                candidates=[
                    ExtractedMemoryCandidate(
                        situation="When running tests in this repo.",
                        lesson="Direct pytest uses the wrong environment.",
                        action="Use uv run pytest.",
                        category="durable_workflow",
                        confidence=0.8,
                        evidence_event_ids=[event.id],
                        evidence_summary="The user gave the durable test command.",
                    )
                ],
                no_memory_reason=None,
            )
        ),
    )

    result = worker.run_once()

    assert result.processed_segments == 1
    assert result.created_candidates == 1
    assert result.remaining_idle_segments == 0
    segment = event_store.list_session_segments()[0]
    assert segment.status == "processed"
    candidate = event_store.list_memory_candidates()[0]
    assert candidate.status == "pending_review"
    assert candidate.source_session_segment_id == segment.id
    assert candidate.evidence_event_ids == [event.id]


def test_extraction_worker_skips_segment_when_no_memory_found(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    event_store.append_event(
        EventCreate(
            event_type="turn_stop",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"status": "done"},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc)
    )
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(candidates=[], no_memory_reason="No reusable lesson.")
        ),
    )

    result = worker.run_once()

    assert result.skipped_segments == 1
    segment = event_store.list_session_segments()[0]
    assert segment.status == "skipped"
    assert segment.error == "No reusable lesson."
    assert event_store.list_memory_candidates() == []


def test_extraction_worker_can_target_one_idle_segment(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    first_event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"prompt": "No memory here."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    target_event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-2",
            payload={"prompt": "Use uv run pytest."},
        ),
        created_at=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 2, tzinfo=timezone.utc)
    )
    target_segment = [
        segment
        for segment in event_store.list_session_segments(status="idle")
        if segment.session_id == "session-2"
    ][0]
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(
                candidates=[
                    ExtractedMemoryCandidate(
                        situation="When running tests in this repo.",
                        lesson="Direct pytest uses the wrong environment.",
                        action="Use uv run pytest.",
                        category="durable_workflow",
                        confidence=0.8,
                        evidence_event_ids=[target_event.id],
                        evidence_summary="The user gave the durable test command.",
                    )
                ],
                no_memory_reason=None,
            )
        ),
    )

    result = worker.run_once(segment_id=target_segment.id)

    assert result.processed_segments == 1
    assert event_store.get_session_segment(target_segment.id).status == "processed"  # type: ignore[union-attr]
    assert event_store.list_memory_candidates()[0].evidence_event_ids == [target_event.id]
    untouched_segments = [
        segment
        for segment in event_store.list_session_segments()
        if segment.session_id == "session-1"
    ]
    assert untouched_segments[0].status == "idle"
    assert first_event.id != target_event.id


def test_extraction_worker_fails_segment_on_unknown_evidence_event(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"prompt": "Use uv run pytest."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc)
    )
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(
                candidates=[
                    ExtractedMemoryCandidate(
                        situation="When running tests in this repo.",
                        lesson="Direct pytest uses the wrong environment.",
                        action="Use uv run pytest.",
                        category="durable_workflow",
                        confidence=0.8,
                        evidence_event_ids=["evt_missing"],
                        evidence_summary="Bad evidence id.",
                    )
                ],
                no_memory_reason=None,
            )
        ),
    )

    result = worker.run_once()

    assert result.failed_segments == 1
    segment = event_store.list_session_segments()[0]
    assert segment.status == "failed"
    assert "evt_missing" in (segment.error or "")
    assert event_store.list_memory_candidates() == []
