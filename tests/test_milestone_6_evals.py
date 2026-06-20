from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory_mcp.core.events import (
    EventCreate,
    EventStore,
)
from memory_mcp.core.models import MemoryCreate, MemoryFeedback, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.extractors import (
    ExtractedMemoryCandidate,
    ExtractionResult,
    StaticMemoryExtractor,
)
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker
from memory_mcp.pipeline.workers.extraction_worker import ExtractionWorker
from memory_mcp.pipeline.workers.session_worker import SessionWorker
from memory_mcp.mcp_server.service import (
    memory_create,
    memory_feedback,
    memory_get,
    memory_search,
)
from memory_mcp.review.service import CandidateReviewService, CandidateUpdate


class EvalEmbedder:
    def embed_text(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            1.0 if any(term in lowered for term in ("pytest", "test", "uv")) else 0.0,
            1.0 if any(term in lowered for term in ("sdk", "openapi", "generated")) else 0.0,
            1.0 if any(term in lowered for term in ("hook", "event", "codex")) else 0.0,
        ]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]


def test_eval_retrieval_prefers_relevant_memory_and_respects_tags(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", EvalEmbedder())
    test_memory = _create_memory(
        store,
        lesson="Direct pytest used the wrong environment.",
        situation="When running tests in this repo.",
        action="Use uv run pytest.",
        tags=["testing"],
    )
    sdk_memory = _create_memory(
        store,
        lesson="Generated SDK files should not be edited directly.",
        situation="When changing generated SDK behavior.",
        action="Update OpenAPI source and regenerate.",
        tags=["sdk"],
    )

    results = store.search_memories("tests fail when I run pytest", limit=2)

    assert results[0].memory.id == test_memory.id
    assert {result.memory.id for result in results} == {test_memory.id, sdk_memory.id}
    assert store.search_memories("tests fail", tags=["sdk"])[0].memory.id == sdk_memory.id


def test_eval_score_updates_cover_positive_negative_and_terminal_signals(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", EvalEmbedder())
    memory = _create_memory(
        store,
        lesson="Use uv run pytest.",
        situation="When running tests.",
        action="Run tests through uv.",
        tags=["testing"],
    )

    assert store.record_feedback(
        MemoryFeedback(memory_id=memory.id, signal="used")
    ).score == pytest.approx(0.6)
    assert store.record_feedback(
        MemoryFeedback(memory_id=memory.id, signal="helpful")
    ).score == pytest.approx(0.85)
    not_helpful = store.record_feedback(
        MemoryFeedback(memory_id=memory.id, signal="not_helpful")
    )
    assert not_helpful is not None
    assert not_helpful.score == pytest.approx(0.65)
    assert not_helpful.status == "active"

    invalid = store.record_feedback(MemoryFeedback(memory_id=memory.id, signal="incorrect"))
    assert invalid is not None
    assert invalid.score == pytest.approx(0.15)
    assert invalid.status == "invalid"
    assert store.search_memories("pytest") == []


def test_eval_dedupe_merges_clear_duplicate_and_rejects_possible_duplicate(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", EvalEmbedder())
    original = _create_memory(
        store,
        lesson="Direct pytest used the wrong environment.",
        situation="When running tests in this repo.",
        action="Use uv run pytest.",
        tags=["testing"],
    )

    merged = _create_memory(
        store,
        lesson="Direct pytest used the wrong environment.",
        situation="When running tests in this repo.",
        action="Use uv run pytest.",
        tags=["python"],
    )
    possible = _create_memory(
        store,
        lesson="Direct pytest used a different interpreter.",
        situation="When running tests in this repo.",
        action="Use uv run pytest.",
        tags=["testing"],
    )

    assert merged.id == original.id
    assert merged.source.extra["dedupe"]["decision"] == "merged_duplicate"
    assert possible.id != original.id
    assert possible.status == "rejected"
    assert possible.source.extra["dedupe"]["decision"] == "possible_duplicate_rejected"
    assert len(store.list_memories(status="active")) == 1


def test_eval_mcp_tool_contract_and_feedback_events(tmp_path) -> None:
    root = tmp_path / "memory"
    store = LocalMemoryStore(root, EvalEmbedder())
    events = EventStore(root)

    created = memory_create(
        store,
        when_useful="When running tests in this repo.",
        details="Direct pytest used the wrong environment. Use uv run pytest.",
        memory_type="feedback",
        tags=["testing"],
        source={"kind": "manual"},
    )
    memory_id = created["memory"]["id"]

    search = memory_search(
        store,
        query="how should tests run?",
        tags=["testing"],
        event_store=events,
        event_context={"project": "/repo", "session_id": "s1", "run_id": "r1"},
    )
    fetched = memory_get(store, memory_id)
    feedback = memory_feedback(
        store,
        memory_id=memory_id,
        signal="helpful",
        context={"project": "/repo", "session_id": "s1", "reason": "Used it."},
        event_store=events,
    )

    assert search["memories"][0].keys() >= {
        "id",
        "when_useful",
        "details",
        "tags",
        "score",
        "confidence",
        "semantic_similarity",
        "final_score",
        "retrieval_reason",
    }
    assert "feedback_guidance" in search
    assert fetched["memory"]["id"] == memory_id
    assert feedback["ok"] is True
    assert [event.event_type for event in events.list_unprocessed()] == [
        "memory_retrieved",
        "memory_feedback",
    ]


def test_eval_llm_candidate_extraction_good_session_vs_no_memory_session(tmp_path) -> None:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    memory_store = LocalMemoryStore(root, EvalEmbedder())
    useful_event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="useful",
            payload={"prompt": "Use uv run pytest in this repo."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    event_store.append_event(
        EventCreate(
            event_type="turn_stop",
            source="test",
            project="/repo",
            session_id="no-memory",
            payload={"status": "done"},
        ),
        created_at=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 2, tzinfo=timezone.utc)
    )

    useful_segment = [
        segment
        for segment in event_store.list_session_segments(status="idle")
        if segment.session_id == "useful"
    ][0]
    created = ExtractionWorker(
        event_store=event_store,
        memory_store=memory_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(
                candidates=[
                    ExtractedMemoryCandidate(
                        situation="When running tests in this repo.",
                        lesson="Direct pytest uses the wrong environment.",
                        action="Use uv run pytest.",
                        memory_type="project",
                        confidence=0.8,
                        evidence_event_ids=[useful_event.id],
                        evidence_summary="The user gave the durable test command.",
                    )
                ],
                no_memory_reason=None,
            )
        ),
    ).run_once(segment_id=useful_segment.id)

    no_memory_segment = [
        segment
        for segment in event_store.list_session_segments(status="idle")
        if segment.session_id == "no-memory"
    ][0]
    skipped = ExtractionWorker(
        event_store=event_store,
        memory_store=memory_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(candidates=[], no_memory_reason="No reusable lesson.")
        ),
    ).run_once(segment_id=no_memory_segment.id)

    assert created.created_candidates == 1
    assert event_store.get_session_segment(useful_segment.id).status == "processed"  # type: ignore[union-attr]
    assert skipped.skipped_segments == 1
    assert event_store.get_session_segment(no_memory_segment.id).status == "skipped"  # type: ignore[union-attr]
    assert len(memory_store.list_memories(status="active")) == 1
    assert memory_store.list_memories(status="active")[0].is_reviewed is False


def test_eval_candidate_review_edit_approve_and_reject_workflows(tmp_path) -> None:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    memory_store = LocalMemoryStore(root, EvalEmbedder())
    service = CandidateReviewService(
        event_store=event_store,
        candidate_worker=CandidateWorker(
            event_store=event_store,
            memory_store=memory_store,
        ),
    )
    approve_candidate = memory_store.create_pending(
        MemoryCreate(
            when_useful="When running tests.",
            details="Use pytest. Run pytest.",
            tags=["testing"],
            confidence=0.5,
            source=MemorySource(
                kind="pipeline_candidate",
                creation_reason="Extractor output.",
                extra={"evidence_summary": "User correction."},
            ),
        )
    )
    reject_candidate = memory_store.create_pending(
        MemoryCreate(
            when_useful="When using docs.",
            details="Do better. Try harder.",
            tags=["weak"],
            confidence=0.2,
            source=MemorySource(
                kind="pipeline_candidate",
                creation_reason="Weak extractor output.",
                extra={"evidence_summary": "No concrete evidence."},
            ),
        )
    )

    memory = service.approve_candidate(
        approve_candidate.id,
        update=CandidateUpdate(
            when_useful="When running tests.",
            details="Direct pytest used the wrong environment. Use uv run pytest.",
            confidence=0.85,
        ),
    )
    rejected = service.reject_candidate(reject_candidate.id, reason="Too vague.")

    assert memory.id == approve_candidate.id
    assert memory.status == "active"
    assert memory.details == "Direct pytest used the wrong environment. Use uv run pytest."
    assert memory.confidence == 0.85
    assert rejected.status == "rejected"
    assert rejected.source.extra["rejection_reason"] == "Too vague."


def _create_memory(
    store: LocalMemoryStore,
    *,
    lesson: str,
    situation: str,
    action: str,
    tags: list[str],
):
    return store.create_memory(
        MemoryCreate(
            when_useful=situation,
            details=f"{lesson} {action}",
            tags=tags,
        )
    )
