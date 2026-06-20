from __future__ import annotations

from datetime import datetime, timezone

from memory_mcp.core.events import EventCreate, EventStore
from memory_mcp.core.models import MemoryCreate, MemoryFeedback, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.extractors import (
    ExtractedMemoryCandidate,
    ExtractionResult,
    StaticMemoryExtractor,
)
from memory_mcp.operator import OperatorWorkflow

from conftest import FakeEmbedder


def test_operator_status_aggregates_runtime_counts(tmp_path) -> None:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    memory_store = LocalMemoryStore(root, FakeEmbedder())
    memory_store.create_memory(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details="Direct pytest used the wrong environment. Use uv run pytest.",
            tags=["testing"],
        )
    )
    invalid = memory_store.create_memory(
        MemoryCreate(
            when_useful="When deploying the billing service.",
            details="A stale deployment note. Use an old command.",
            tags=["deploy"],
        )
    )
    memory_store.record_feedback(
        MemoryFeedback(memory_id=invalid.id, signal="incorrect")
    )
    event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="s1",
            payload={"prompt": "Use uv run pytest."},
        )
    )
    memory_store.create_pending(
        MemoryCreate(
            when_useful="When running tests.",
            details="Use uv. Run uv run pytest.",
            tags=["testing"],
            source=MemorySource(
                kind="pipeline_candidate",
                creation_reason="Test setup.",
                extra={"evidence_summary": "User correction.", "category": "testing"},
            ),
        )
    )

    status = OperatorWorkflow(
        root=root,
        event_store=event_store,
        memory_store=memory_store,
    ).status()

    assert status.events["unprocessed"] == 1
    assert status.events["total"] == 1
    assert status.candidates["pending_review"] == 1
    assert status.memories["active"] == 1
    assert status.memories["invalid"] == 1


def test_operator_process_runs_events_sessions_and_extraction(tmp_path) -> None:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    memory_store = LocalMemoryStore(root, FakeEmbedder())
    event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="s1",
            payload={"prompt": "Use uv run pytest."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    extractor = StaticMemoryExtractor(
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
    )

    result = OperatorWorkflow(
        root=root,
        event_store=event_store,
        memory_store=memory_store,
    ).process(
        extractor=extractor,
        idle_after_seconds=0,
        extraction_limit=1,
        apply_decay=False,
    )

    assert result.events["processed"] == 1
    assert result.sessions["upserted_segments"] == 1
    assert result.extraction["created_candidates"] == 1
    assert result.decay["decayed"] == 0
    assert result.status.events["unprocessed"] == 0
    assert result.status.sessions["processed"] == 1
    assert result.status.candidates["pending_review"] == 1
