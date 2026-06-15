from __future__ import annotations

from memory_mcp.core.events import (
    EventStore,
    MemoryCandidateCreate,
    MemoryCandidateRecord,
    utc_now,
)
from memory_mcp.core.models import MemoryCreate, MemoryRecord, MemorySource
from memory_mcp.core.store import LocalMemoryStore


class CandidateWorker:
    def __init__(
        self,
        *,
        event_store: EventStore,
        memory_store: LocalMemoryStore,
    ) -> None:
        self.event_store = event_store
        self.memory_store = memory_store

    def create_candidate(
        self,
        candidate: MemoryCandidateCreate,
    ) -> MemoryCandidateRecord:
        return self.event_store.create_memory_candidate(candidate)

    def list_candidates(
        self,
        *,
        status: str | None = None,
    ) -> list[MemoryCandidateRecord]:
        return self.event_store.list_memory_candidates(status=status)

    def get_candidate(self, candidate_id: str) -> MemoryCandidateRecord | None:
        return self.event_store.get_memory_candidate(candidate_id)

    def update_candidate(
        self,
        candidate_id: str,
        *,
        situation: str | None = None,
        lesson: str | None = None,
        action: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        creation_reason: str | None = None,
        evidence_event_ids: list[str] | None = None,
        evidence_summary: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MemoryCandidateRecord:
        candidate = self._require_candidate(candidate_id)
        if candidate.status != "pending_review":
            raise ValueError(f"candidate is not pending_review: {candidate.status}")

        update: dict[str, object] = {}
        for key, value in {
            "situation": situation,
            "lesson": lesson,
            "action": action,
            "category": category,
            "confidence": confidence,
            "creation_reason": creation_reason,
            "evidence_event_ids": evidence_event_ids,
            "evidence_summary": evidence_summary,
            "metadata": metadata,
        }.items():
            if value is not None:
                update[key] = value

        updated = candidate.model_copy(update=update)
        self.event_store.update_memory_candidate(updated)
        stored = self.event_store.get_memory_candidate(candidate_id)
        if stored is None:
            raise ValueError(f"candidate not found after update: {candidate_id}")
        return stored

    def approve_candidate(self, candidate_id: str) -> tuple[MemoryCandidateRecord, MemoryRecord]:
        candidate = self._require_candidate(candidate_id)
        if candidate.status != "pending_review":
            raise ValueError(f"candidate is not pending_review: {candidate.status}")

        memory = self.memory_store.create_memory(_candidate_to_memory_create(candidate))
        now = utc_now()
        dedupe = memory.source.extra.get("dedupe", {})
        if memory.status == "rejected":
            updated = candidate.model_copy(
                update={
                    "status": "rejected",
                    "updated_at": now,
                    "rejected_at": now,
                    "approved_memory_id": memory.id,
                    "rejection_reason": dedupe.get(
                        "reason",
                        "Candidate was rejected by memory dedupe rules.",
                    ),
                }
            )
        elif dedupe.get("decision") == "merged_duplicate":
            updated = candidate.model_copy(
                update={
                    "status": "merged",
                    "updated_at": now,
                    "approved_at": now,
                    "approved_memory_id": memory.id,
                }
            )
        else:
            updated = candidate.model_copy(
                update={
                    "status": "approved",
                    "updated_at": now,
                    "approved_at": now,
                    "approved_memory_id": memory.id,
                }
            )

        self.event_store.update_memory_candidate(updated)
        return updated, memory

    def reject_candidate(
        self,
        candidate_id: str,
        *,
        reason: str,
    ) -> MemoryCandidateRecord:
        candidate = self._require_candidate(candidate_id)
        if candidate.status != "pending_review":
            raise ValueError(f"candidate is not pending_review: {candidate.status}")

        now = utc_now()
        updated = candidate.model_copy(
            update={
                "status": "rejected",
                "updated_at": now,
                "rejected_at": now,
                "rejection_reason": reason,
            }
        )
        self.event_store.update_memory_candidate(updated)
        return updated

    def retry_candidate(self, candidate_id: str) -> MemoryCandidateRecord:
        candidate = self._require_candidate(candidate_id)
        if candidate.status not in {"rejected", "merged"}:
            raise ValueError(f"candidate cannot be retried from status: {candidate.status}")

        updated = candidate.model_copy(
            update={
                "status": "pending_review",
                "updated_at": utc_now(),
                "approved_at": None,
                "approved_memory_id": None,
                "rejected_at": None,
                "rejection_reason": None,
                "merged_into_candidate_id": None,
            }
        )
        self.event_store.update_memory_candidate(updated)
        return updated

    def _require_candidate(self, candidate_id: str) -> MemoryCandidateRecord:
        candidate = self.event_store.get_memory_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        return candidate


def _candidate_to_memory_create(candidate: MemoryCandidateRecord) -> MemoryCreate:
    return MemoryCreate(
        what_happened=candidate.lesson,
        when_useful=candidate.situation,
        helpful_explanation=candidate.action,
        tags=[candidate.category],
        confidence=candidate.confidence,
        source=MemorySource(
            kind="pipeline_candidate",
            evidence_event_ids=candidate.evidence_event_ids,
            creation_reason=candidate.creation_reason,
            extra={
                "candidate_id": candidate.id,
                "source_session_segment_id": candidate.source_session_segment_id,
                "evidence_summary": candidate.evidence_summary,
                "candidate_metadata": candidate.metadata,
            },
        ),
    )
