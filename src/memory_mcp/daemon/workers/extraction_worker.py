from __future__ import annotations

from dataclasses import dataclass

from memory_mcp.core.events import EventRecord, EventStore, MemoryCandidateCreate, SessionSegmentRecord
from memory_mcp.daemon.extractors import ExtractionResult, MemoryExtractor


@dataclass(frozen=True)
class ExtractionWorkerResult:
    processed_segments: int
    skipped_segments: int
    failed_segments: int
    created_candidates: int
    remaining_idle_segments: int


class ExtractionWorker:
    def __init__(
        self,
        *,
        event_store: EventStore,
        extractor: MemoryExtractor,
    ) -> None:
        self.event_store = event_store
        self.extractor = extractor

    def run_once(
        self,
        *,
        limit: int = 1,
        segment_id: str | None = None,
    ) -> ExtractionWorkerResult:
        processed = 0
        skipped = 0
        failed = 0
        created = 0

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
                failed += 1
                self.event_store.mark_session_segment_status(
                    segment.id,
                    "failed",
                    error=str(exc),
                )
                continue

            if created_for_segment:
                processed += 1
                created += created_for_segment
                self.event_store.mark_session_segment_status(segment.id, "processed")
            else:
                skipped += 1
                reason = result.no_memory_reason or "No durable memory candidate found."
                self.event_store.mark_session_segment_status(
                    segment.id,
                    "skipped",
                    error=reason,
                )

        return ExtractionWorkerResult(
            processed_segments=processed,
            skipped_segments=skipped,
            failed_segments=failed,
            created_candidates=created,
            remaining_idle_segments=len(
                self.event_store.list_session_segments(status="idle")
            ),
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
            self.event_store.create_memory_candidate(
                MemoryCandidateCreate(
                    situation=candidate.situation,
                    lesson=candidate.lesson,
                    action=candidate.action,
                    category=candidate.category,
                    confidence=candidate.confidence,
                    creation_reason="Extracted from idle session segment by LLM.",
                    evidence_event_ids=candidate.evidence_event_ids,
                    evidence_summary=candidate.evidence_summary,
                    source_session_segment_id=segment.id,
                    metadata={"extractor": "llm", "no_memory_reason": result.no_memory_reason},
                )
            )
            count += 1
        return count
