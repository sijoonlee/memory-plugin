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

        memory = self.memory_store.create_memory(
            _candidate_to_memory_create(candidate, project=self._candidate_project(candidate))
        )
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

    def merge_candidates(
        self,
        source_ids: list[str],
        merged: MemoryCandidateCreate,
    ) -> MemoryCandidateRecord:
        """Combine several pending candidates into one new pending candidate.

        The merged candidate is human- or agent-authored content; provenance is
        derived from the sources (the union of evidence event ids, the set of
        source segment ids, and the source candidate ids land in metadata). Each
        source is marked ``merged`` with ``merged_into_candidate_id``. The new
        candidate is itself ``pending_review`` and stays editable before approval.
        """

        unique_ids = list(dict.fromkeys(source_ids))
        if len(unique_ids) < 2:
            raise ValueError("merge requires at least two distinct source candidates")
        sources = [self._require_candidate(candidate_id) for candidate_id in unique_ids]
        for source in sources:
            if source.status != "pending_review":
                raise ValueError(
                    f"candidate is not pending_review: {source.id} ({source.status})"
                )

        evidence_event_ids = sorted(
            {eid for source in sources for eid in source.evidence_event_ids}
        )
        source_segment_ids = sorted(
            {
                source.source_session_segment_id
                for source in sources
                if source.source_session_segment_id is not None
            }
        )
        metadata = dict(merged.metadata)
        metadata["merged_from"] = {
            "source_candidate_ids": unique_ids,
            "source_session_segment_ids": source_segment_ids,
        }

        new_candidate = self.event_store.create_memory_candidate(
            merged.model_copy(
                update={
                    "evidence_event_ids": evidence_event_ids,
                    "source_session_segment_id": None,
                    "metadata": metadata,
                }
            )
        )

        now = utc_now()
        for source in sources:
            self.event_store.update_memory_candidate(
                source.model_copy(
                    update={
                        "status": "merged",
                        "updated_at": now,
                        "merged_into_candidate_id": new_candidate.id,
                    }
                )
            )
        return new_candidate

    def archive_candidate(self, candidate_id: str) -> MemoryCandidateRecord:
        """Hide a candidate from the active queue while retaining it for audit."""

        candidate = self._require_candidate(candidate_id)
        if candidate.status == "archived":
            raise ValueError("candidate is already archived")
        updated = candidate.model_copy(
            update={"status": "archived", "updated_at": utc_now()}
        )
        self.event_store.update_memory_candidate(updated)
        return updated

    def _require_candidate(self, candidate_id: str) -> MemoryCandidateRecord:
        candidate = self.event_store.get_memory_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        return candidate

    def _candidate_project(self, candidate: MemoryCandidateRecord) -> str | None:
        """Derive the repo scope for an approved candidate from its source segment.

        A merged candidate has no single source segment; the project then stays
        ``None`` (global) since the merge may span repos.
        """

        if candidate.source_session_segment_id is None:
            return None
        segment = self.event_store.get_session_segment(candidate.source_session_segment_id)
        return segment.project if segment is not None else None


def _candidate_to_memory_create(
    candidate: MemoryCandidateRecord,
    *,
    project: str | None = None,
) -> MemoryCreate:
    return MemoryCreate(
        what_happened=candidate.lesson,
        when_useful=candidate.situation,
        helpful_explanation=candidate.action,
        tags=[candidate.category],
        confidence=candidate.confidence,
        project=project,
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
