from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from memory_mcp.core.embeddings import LangChainHuggingFaceEmbedder
from memory_mcp.core.events import (
    EventRecord,
    EventStore,
    SessionSegmentRecord,
)
from memory_mcp.core.models import MemoryCreate, MemoryRecord
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker

# After M18-1 a "candidate" is a ``pending_review`` memory. The review surface
# keeps the candidate vocabulary, but operates on ``MemoryRecord``; the
# extractor's provenance (segment id, evidence summary) lives in
# ``memory.source.extra``.


def _candidate_type(memory: MemoryRecord) -> str | None:
    """The memory's constrained ``memory_type`` (M19) for review/filtering."""

    return memory.memory_type


def _candidate_segment_id(memory: MemoryRecord) -> str | None:
    segment_id = memory.source.extra.get("source_session_segment_id")
    return segment_id if isinstance(segment_id, str) else None


class CandidateUpdate(BaseModel):
    when_useful: str | None = None
    details: str | None = None
    tags: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class CandidateFilters(BaseModel):
    status: str | None = "pending_review"
    project: str | None = None
    memory_type: str | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    created_from: datetime | None = None
    created_to: datetime | None = None


class CandidateDetail(BaseModel):
    candidate: MemoryRecord
    source_segment: SessionSegmentRecord | None = None
    evidence_events: list[EventRecord]
    segment_events: list[EventRecord] = Field(default_factory=list)


class SegmentDetail(BaseModel):
    segment: SessionSegmentRecord
    events: list[EventRecord] = Field(default_factory=list)


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
    ) -> list[MemoryRecord]:
        filters = filters or CandidateFilters()
        candidates = self.candidate_worker.list_candidates(status=filters.status)
        return [
            candidate
            for candidate in candidates
            if self._matches_filters(candidate, filters)
        ]

    def list_active_memories(self) -> list[MemoryRecord]:
        """Return active memories for read-only inspection in the review UI.

        These come from the memory store (manual ``memory_create`` calls and
        approved candidates) and are intentionally not editable here.
        """

        return self.candidate_worker.memory_store.list_memories(status="active")

    def list_memories(
        self,
        *,
        status: str | None = "active",
        is_reviewed: bool | None = None,
        manual: bool | None = None,
        memory_type: str | None = None,
    ) -> list[MemoryRecord]:
        """Memory-manager listing (M18-3).

        Filters: ``status`` (active/archived/…), ``is_reviewed`` (the read/unread
        inbox), ``manual`` (origin = manual ``memory_create``, derived from
        ``source.kind``), and ``memory_type`` (M19 taxonomy:
        user/feedback/project/reference). The unread inbox is
        ``status='active', is_reviewed=False``.
        """

        memories = self.candidate_worker.memory_store.list_memories(
            status=status,
            is_reviewed=is_reviewed,
        )
        if manual is not None:
            memories = [
                memory
                for memory in memories
                if (memory.source.kind == "manual") == manual
            ]
        if memory_type is not None:
            memories = [
                memory for memory in memories if memory.memory_type == memory_type
            ]
        return memories

    def set_reviewed(self, memory_id: str, value: bool) -> MemoryRecord:
        updated = self.candidate_worker.memory_store.set_reviewed(memory_id, value)
        if updated is None:
            raise ValueError(f"memory not found: {memory_id}")
        return updated

    def archive_memory(self, memory_id: str) -> MemoryRecord:
        updated = self.candidate_worker.memory_store.archive_memory(memory_id)
        if updated is None:
            raise ValueError(f"memory not found: {memory_id}")
        return updated

    def restore_memory(self, memory_id: str) -> MemoryRecord:
        updated = self.candidate_worker.memory_store.restore_memory(memory_id)
        if updated is None:
            raise ValueError(f"memory not found: {memory_id}")
        return updated

    def delete_memory(self, memory_id: str) -> bool:
        return self.candidate_worker.memory_store.delete_memory(memory_id)

    def get_memory_detail(self, memory_id: str) -> MemoryRecord:
        """Fetch one active memory by id for read-only display."""

        memory = self.candidate_worker.memory_store.get_memory(memory_id)
        if memory is None:
            raise ValueError(f"memory not found: {memory_id}")
        return memory

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
            evidence_events=self.event_store.list_events_by_ids(
                candidate.source.evidence_event_ids
            ),
            segment_events=segment_events,
        )

    def update_candidate(
        self,
        candidate_id: str,
        update: CandidateUpdate,
    ) -> MemoryRecord:
        return self.candidate_worker.update_candidate(
            candidate_id,
            **update.model_dump(exclude_unset=True),
        )

    def approve_candidate(
        self,
        candidate_id: str,
        *,
        update: CandidateUpdate | None = None,
    ) -> MemoryRecord:
        if update is not None and update.model_fields_set:
            self.update_candidate(candidate_id, update)
        return self.candidate_worker.approve_candidate(candidate_id)

    def reject_candidate(self, candidate_id: str, *, reason: str) -> MemoryRecord:
        return self.candidate_worker.reject_candidate(candidate_id, reason=reason)

    def merge_candidates(
        self,
        source_ids: list[str],
        merged: MemoryCreate,
    ) -> MemoryRecord:
        """Merge pending candidates into one new editable pending candidate.

        Human-driven: the new candidate still requires approval, which runs the
        normal memory creation path (including dedupe).
        """

        return self.candidate_worker.merge_candidates(source_ids, merged)

    def archive_candidate(self, candidate_id: str) -> MemoryRecord:
        return self.candidate_worker.archive_candidate(candidate_id)

    def list_segments(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SessionSegmentRecord]:
        """List session segments by status, including skipped/failed ones.

        Unlike candidate listing, this surfaces every segment (with its
        ``error`` reason) regardless of whether it produced a candidate.
        """

        segments = self.event_store.list_session_segments(status=status)
        return segments[:limit]

    def get_segment_detail(self, segment_id: str) -> SegmentDetail:
        """Fetch a segment with its raw event log.

        Kept separate from listing because hook event logs can be noisy or
        sensitive, so the event payloads are only loaded on explicit request.
        """

        segment = self.event_store.get_session_segment(segment_id)
        if segment is None:
            raise ValueError(f"session segment not found: {segment_id}")
        return SegmentDetail(
            segment=segment,
            events=self.event_store.list_events_for_session_segment(segment),
        )

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

    def _require_candidate(self, candidate_id: str) -> MemoryRecord:
        candidate = self.candidate_worker.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        return candidate

    def _source_segment(
        self,
        candidate: MemoryRecord,
    ) -> SessionSegmentRecord | None:
        segment_id = _candidate_segment_id(candidate)
        if segment_id is None:
            return None
        return self.event_store.get_session_segment(segment_id)

    def _matches_filters(
        self,
        candidate: MemoryRecord,
        filters: CandidateFilters,
    ) -> bool:
        if filters.memory_type is not None and _candidate_type(candidate) != filters.memory_type:
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
        if filters.project is not None and candidate.project != filters.project:
            return False
        return True
