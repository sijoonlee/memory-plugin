from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory_mcp.core.events import EventCreate, EventStore, MemoryCandidateCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.daemon.workers.candidate_worker import CandidateWorker
from memory_mcp.daemon.workers.session_worker import SessionWorker

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
    candidate = event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests in this repo.",
            lesson="Direct pytest uses the wrong environment.",
            action="Use uv run pytest.",
            category="durable_workflow",
            confidence=0.8,
            creation_reason="User correction in session.",
            evidence_event_ids=["evt_1"],
            evidence_summary="The user corrected the test command.",
        )
    )

    updated, memory = worker.approve_candidate(candidate.id)

    assert updated.status == "approved"
    assert updated.approved_memory_id == memory.id
    assert memory_store.get_memory(memory.id) is not None
    assert memory.when_useful == "When running tests in this repo."
    assert memory.what_happened == "Direct pytest uses the wrong environment."
    assert memory.source.kind == "daemon_candidate"
    assert memory.source.evidence_event_ids == ["evt_1"]


def test_candidate_worker_rejects_and_retries_candidate(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    worker = CandidateWorker(event_store=event_store, memory_store=memory_store)
    candidate = event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When using local docs.",
            lesson="A vague lesson.",
            action="Do something.",
            category="user_correction",
            creation_reason="Weak extractor output.",
            evidence_summary="No concrete evidence.",
        )
    )

    rejected = worker.reject_candidate(candidate.id, reason="Too vague.")
    retried = worker.retry_candidate(candidate.id)

    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "Too vague."
    assert retried.status == "pending_review"
    assert retried.rejection_reason is None
    assert retried.rejected_at is None
