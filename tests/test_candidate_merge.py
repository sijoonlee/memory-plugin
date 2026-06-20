from __future__ import annotations

import pytest

from memory_mcp.core.events import EventStore
from memory_mcp.core.models import MemoryCreate, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker

from conftest import FakeEmbedder


def _worker(tmp_path) -> CandidateWorker:
    return CandidateWorker(
        event_store=EventStore(tmp_path / "events"),
        memory_store=LocalMemoryStore(tmp_path / "memory", FakeEmbedder()),
    )


def _candidate(
    worker: CandidateWorker,
    *,
    lesson: str,
    evidence: list[str],
    segment: str,
) -> str:
    record = worker.create_candidate(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details=f"{lesson} Use uv run pytest.",
            tags=["durable_workflow"],
            confidence=0.7,
            source=MemorySource(
                kind="pipeline_candidate",
                evidence_event_ids=evidence,
                creation_reason="User correction in session.",
                extra={
                    "source_session_segment_id": segment,
                    "evidence_summary": "The user corrected the test command.",
                },
            ),
        )
    )
    return record.id


def _merged_content() -> MemoryCreate:
    return MemoryCreate(
        when_useful="When running tests in this repo.",
        details="Run tests through the project environment. Use uv run pytest so dependencies resolve.",
        tags=["durable_workflow"],
        confidence=0.8,
        source=MemorySource(
            kind="pipeline_merge",
            creation_reason="Merged from repeated test-command corrections.",
            extra={
                "evidence_summary": "The user corrected the test command across sessions.",
            },
        ),
    )


def test_merge_preserves_provenance_and_marks_sources(tmp_path) -> None:
    worker = _worker(tmp_path)
    a = _candidate(worker, lesson="pytest fails directly.", evidence=["evt_1", "evt_2"], segment="seg_a")
    b = _candidate(worker, lesson="dependency errors in tests.", evidence=["evt_2", "evt_3"], segment="seg_b")

    merged = worker.merge_candidates([a, b], _merged_content())

    # New candidate is pending_review and still editable.
    assert merged.status == "pending_review"
    # Union of evidence event ids across sources.
    assert merged.source.evidence_event_ids == ["evt_1", "evt_2", "evt_3"]
    # Source provenance lands in source.extra.
    assert merged.source.extra["merged_from"]["source_candidate_ids"] == [a, b]
    assert merged.source.extra["merged_from"]["source_session_segment_ids"] == ["seg_a", "seg_b"]
    assert "source_session_segment_id" not in merged.source.extra

    # Each source is now merged and points at the new candidate.
    for source_id in (a, b):
        source = worker.get_candidate(source_id)
        assert source is not None
        assert source.status == "merged"
        assert source.source.extra["merged_into_candidate_id"] == merged.id


def test_merged_candidate_is_editable(tmp_path) -> None:
    worker = _worker(tmp_path)
    a = _candidate(worker, lesson="one", evidence=["evt_1"], segment="seg_a")
    b = _candidate(worker, lesson="two", evidence=["evt_2"], segment="seg_b")
    merged = worker.merge_candidates([a, b], _merged_content())

    edited = worker.update_candidate(merged.id, details="A clearer merged lesson.")
    assert edited.details == "A clearer merged lesson."
    # Editing does not disturb derived provenance.
    assert edited.source.evidence_event_ids == ["evt_1", "evt_2"]


def test_merge_requires_two_distinct_sources(tmp_path) -> None:
    worker = _worker(tmp_path)
    a = _candidate(worker, lesson="one", evidence=["evt_1"], segment="seg_a")
    with pytest.raises(ValueError, match="at least two distinct"):
        worker.merge_candidates([a, a], _merged_content())


def test_merge_rejects_non_pending_source(tmp_path) -> None:
    worker = _worker(tmp_path)
    a = _candidate(worker, lesson="one", evidence=["evt_1"], segment="seg_a")
    b = _candidate(worker, lesson="two", evidence=["evt_2"], segment="seg_b")
    worker.reject_candidate(b, reason="not useful")

    with pytest.raises(ValueError, match="not pending_review"):
        worker.merge_candidates([a, b], _merged_content())


def test_archive_hides_candidate_but_retains_it(tmp_path) -> None:
    worker = _worker(tmp_path)
    a = _candidate(worker, lesson="noisy", evidence=["evt_1"], segment="seg_a")

    archived = worker.archive_candidate(a)
    assert archived.status == "archived"

    # Excluded from the pending queue, still fetchable and listed under archived.
    pending_ids = [c.id for c in worker.list_candidates(status="pending_review")]
    assert a not in pending_ids
    assert worker.get_candidate(a) is not None
    archived_ids = [c.id for c in worker.list_candidates(status="archived")]
    assert archived_ids == [a]


def test_archive_twice_is_rejected(tmp_path) -> None:
    worker = _worker(tmp_path)
    a = _candidate(worker, lesson="noisy", evidence=["evt_1"], segment="seg_a")
    worker.archive_candidate(a)
    with pytest.raises(ValueError, match="already archived"):
        worker.archive_candidate(a)
