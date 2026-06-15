from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from memory_mcp.core.embeddings import LangChainHuggingFaceEmbedder
from memory_mcp.core.events import (
    EventRecord,
    EventStore,
    MemoryCandidateRecord,
    SessionSegmentRecord,
)
from memory_mcp.core.models import MemoryRecord
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker


class CandidateUpdate(BaseModel):
    situation: str | None = None
    lesson: str | None = None
    action: str | None = None
    category: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    creation_reason: str | None = None
    evidence_event_ids: list[str] | None = None
    evidence_summary: str | None = None
    metadata: dict[str, Any] | None = None


class CandidateFilters(BaseModel):
    status: str | None = "pending_review"
    project: str | None = None
    category: str | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_from: datetime | None = None
    created_to: datetime | None = None


class CandidateDetail(BaseModel):
    candidate: MemoryCandidateRecord
    source_segment: SessionSegmentRecord | None = None
    evidence_events: list[EventRecord]
    segment_events: list[EventRecord] = Field(default_factory=list)


class CandidateReviewService:
    def __init__(
        self,
        *,
        event_store: EventStore,
        candidate_worker: CandidateWorker,
    ) -> None:
        self.event_store = event_store
        self.candidate_worker = candidate_worker

    @classmethod
    def from_root(cls, root: Path | str) -> "CandidateReviewService":
        event_store = EventStore(root)
        memory_store = LocalMemoryStore(
            root=root,
            embedder=LangChainHuggingFaceEmbedder(),
        )
        return cls(
            event_store=event_store,
            candidate_worker=CandidateWorker(
                event_store=event_store,
                memory_store=memory_store,
            ),
        )

    def list_candidates(
        self,
        *,
        filters: CandidateFilters | None = None,
    ) -> list[MemoryCandidateRecord]:
        filters = filters or CandidateFilters()
        candidates = self.event_store.list_memory_candidates(status=filters.status)
        return [
            candidate
            for candidate in candidates
            if self._matches_filters(candidate, filters)
        ]

    def get_candidate_detail(
        self,
        candidate_id: str,
        *,
        include_segment_events: bool = False,
    ) -> CandidateDetail:
        candidate = self._require_candidate(candidate_id)
        source_segment = self._source_segment(candidate)
        segment_events: list[EventRecord] = []
        if include_segment_events and source_segment is not None:
            segment_events = self.event_store.list_events_for_session_segment(source_segment)
        return CandidateDetail(
            candidate=candidate,
            source_segment=source_segment,
            evidence_events=self.event_store.list_events_by_ids(candidate.evidence_event_ids),
            segment_events=segment_events,
        )

    def update_candidate(
        self,
        candidate_id: str,
        update: CandidateUpdate,
    ) -> MemoryCandidateRecord:
        return self.candidate_worker.update_candidate(
            candidate_id,
            **update.model_dump(exclude_unset=True),
        )

    def approve_candidate(
        self,
        candidate_id: str,
        *,
        update: CandidateUpdate | None = None,
    ) -> tuple[MemoryCandidateRecord, MemoryRecord]:
        if update is not None and update.model_fields_set:
            self.update_candidate(candidate_id, update)
        return self.candidate_worker.approve_candidate(candidate_id)

    def reject_candidate(self, candidate_id: str, *, reason: str) -> MemoryCandidateRecord:
        return self.candidate_worker.reject_candidate(candidate_id, reason=reason)

    def retry_segment(self, segment_id: str) -> SessionSegmentRecord:
        segment = self.event_store.get_session_segment(segment_id)
        if segment is None:
            raise ValueError(f"session segment not found: {segment_id}")
        if segment.status not in {"failed", "skipped"}:
            raise ValueError(f"session segment cannot be retried from status: {segment.status}")
        self.event_store.mark_session_segment_status(segment_id, "idle", error=None)
        retried = self.event_store.get_session_segment(segment_id)
        if retried is None:
            raise ValueError(f"session segment not found after retry: {segment_id}")
        return retried

    def _require_candidate(self, candidate_id: str) -> MemoryCandidateRecord:
        candidate = self.event_store.get_memory_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        return candidate

    def _source_segment(
        self,
        candidate: MemoryCandidateRecord,
    ) -> SessionSegmentRecord | None:
        if candidate.source_session_segment_id is None:
            return None
        return self.event_store.get_session_segment(candidate.source_session_segment_id)

    def _matches_filters(
        self,
        candidate: MemoryCandidateRecord,
        filters: CandidateFilters,
    ) -> bool:
        if filters.category is not None and candidate.category != filters.category:
            return False
        if (
            filters.min_confidence is not None
            and candidate.confidence < filters.min_confidence
        ):
            return False
        if filters.created_from is not None and candidate.created_at < filters.created_from:
            return False
        if filters.created_to is not None and candidate.created_at > filters.created_to:
            return False
        if filters.project is not None:
            segment = self._source_segment(candidate)
            if segment is None or segment.project != filters.project:
                return False
        return True
