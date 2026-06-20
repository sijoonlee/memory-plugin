from __future__ import annotations

from dataclasses import dataclass, field

from memory_mcp.core.events import EventRecord, EventStore, SessionSegmentRecord
from memory_mcp.core.models import MemoryCreate, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.extractors import (
    ExtractionResult,
    MemoryExtractor,
    compose_details,
)


@dataclass(frozen=True)
class SegmentOutcome:
    segment_id: str
    session_id: str
    reason: str


@dataclass(frozen=True)
class ExtractionWorkerResult:
    processed_segments: int
    skipped_segments: int
    failed_segments: int
    created_candidates: int
    remaining_idle_segments: int
    # Why each non-processed segment produced no memory: skipped carries the
    # LLM's no_memory_reason, failed carries the extraction error.
    skipped: list[SegmentOutcome] = field(default_factory=list)
    failed: list[SegmentOutcome] = field(default_factory=list)


class ExtractionWorker:
    def __init__(
        self,
        *,
        event_store: EventStore,
        memory_store: LocalMemoryStore,
        extractor: MemoryExtractor,
    ) -> None:
        self.event_store = event_store
        self.memory_store = memory_store
        self.extractor = extractor

    def run_once(
        self,
        *,
        limit: int = 1,
        segment_id: str | None = None,
    ) -> ExtractionWorkerResult:
        processed = 0
        created = 0
        skipped_outcomes: list[SegmentOutcome] = []
        failed_outcomes: list[SegmentOutcome] = []

        if segment_id is None:
            segments = self.event_store.list_session_segments(status="idle")[:limit]
        else:
            segment = self.event_store.get_session_segment(segment_id)
            segments = [] if segment is None or segment.status != "idle" else [segment]

        for segment in segments:
            events = self.event_store.list_events_for_session_segment(segment)
            try:
                result = self.extractor.extract(segment=segment, events=events)
                created_for_segment = self._create_candidates(
                    segment=segment,
                    events=events,
                    result=result,
                )
            except Exception as exc:  # pragma: no cover - defensive safety path
                error = str(exc)
                failed_outcomes.append(
                    SegmentOutcome(segment.id, segment.session_id, error)
                )
                self.event_store.mark_session_segment_status(
                    segment.id,
                    "failed",
                    error=error,
                )
                continue

            if created_for_segment:
                processed += 1
                created += created_for_segment
                self.event_store.mark_session_segment_status(segment.id, "processed")
            else:
                reason = result.no_memory_reason or "No durable memory candidate found."
                skipped_outcomes.append(
                    SegmentOutcome(segment.id, segment.session_id, reason)
                )
                self.event_store.mark_session_segment_status(
                    segment.id,
                    "skipped",
                    error=reason,
                )

        return ExtractionWorkerResult(
            processed_segments=processed,
            skipped_segments=len(skipped_outcomes),
            failed_segments=len(failed_outcomes),
            created_candidates=created,
            remaining_idle_segments=len(
                self.event_store.list_session_segments(status="idle")
            ),
            skipped=skipped_outcomes,
            failed=failed_outcomes,
        )

    def _create_candidates(
        self,
        *,
        segment: SessionSegmentRecord,
        events: list[EventRecord],
        result: ExtractionResult,
    ) -> int:
        count = 0
        event_ids = {event.id for event in events}
        for candidate in result.candidates:
            unknown_ids = set(candidate.evidence_event_ids) - event_ids
            if unknown_ids:
                raise ValueError(
                    "candidate referenced evidence events outside the segment: "
                    + ", ".join(sorted(unknown_ids))
                )
            # M18-3: no approval gate — extraction creates an active memory
            # directly (redacted + embedded + deduped). It is immediately
            # searchable and starts unread (is_reviewed=False) for the inbox.
            # The extractor's situation/lesson/action map onto when_useful/details;
            # memory_type (M19) is the constrained type; raw fields stay in
            # source.extra.
            self.memory_store.create_memory(
                MemoryCreate(
                    when_useful=candidate.situation,
                    details=compose_details(candidate.lesson, candidate.action),
                    memory_type=candidate.memory_type,
                    confidence=candidate.confidence,
                    project=segment.project,
                    source=MemorySource(
                        kind="pipeline_candidate",
                        evidence_event_ids=candidate.evidence_event_ids,
                        creation_reason="Extracted from idle session segment by LLM.",
                        extra={
                            "source_session_segment_id": segment.id,
                            "evidence_summary": candidate.evidence_summary,
                            "situation": candidate.situation,
                            "lesson": candidate.lesson,
                            "action": candidate.action,
                            "memory_type": candidate.memory_type,
                            "extractor": "llm",
                            "no_memory_reason": result.no_memory_reason,
                        },
                    ),
                )
            )
            count += 1
        return count
