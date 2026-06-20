from __future__ import annotations

from memory_mcp.core.content import build_content_for_embedding
from memory_mcp.core.events import EventStore, utc_now
from memory_mcp.core.models import MemoryCreate, MemoryRecord
from memory_mcp.core.store import LocalMemoryStore

# After the M18-1 unification a "candidate" is just a ``pending_review`` memory:
# one model, one store. This worker owns the pending-memory lifecycle
# (create / edit / approve / reject / retry / merge / archive); ``EventStore`` is
# retained only to resolve session segments referenced by a memory's provenance.


class CandidateWorker:
    def __init__(
        self,
        *,
        event_store: EventStore,
        memory_store: LocalMemoryStore,
    ) -> None:
        self.event_store = event_store
        self.memory_store = memory_store

    def create_candidate(self, memory: MemoryCreate) -> MemoryRecord:
        return self.memory_store.create_pending(memory)

    def list_candidates(
        self,
        *,
        status: str | None = None,
    ) -> list[MemoryRecord]:
        return self.memory_store.list_memories(status=status)

    def get_candidate(self, candidate_id: str) -> MemoryRecord | None:
        return self.memory_store.get_memory(candidate_id)

    def update_candidate(
        self,
        candidate_id: str,
        *,
        when_useful: str | None = None,
        details: str | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
    ) -> MemoryRecord:
        record = self._require_pending(candidate_id)

        update: dict[str, object] = {}
        for key, value in {
            "when_useful": when_useful,
            "details": details,
            "tags": tags,
            "confidence": confidence,
        }.items():
            if value is not None:
                update[key] = value

        updated = record.model_copy(update=update)
        # Keep the embedding text in sync with edited body/tags; it is what
        # ``activate`` embeds.
        updated = updated.model_copy(
            update={
                "content_for_embedding": build_content_for_embedding(
                    when_useful=updated.when_useful,
                    details=updated.details,
                    tags=updated.tags,
                ),
                "updated_at": utc_now(),
            }
        )
        self.memory_store.update_memory(updated)
        stored = self.memory_store.get_memory(candidate_id)
        if stored is None:
            raise ValueError(f"candidate not found after update: {candidate_id}")
        return stored

    def approve_candidate(self, candidate_id: str) -> MemoryRecord:
        """Activate a pending memory: embed + dedupe.

        The resulting status is ``active`` (kept), ``merged`` (folded into an
        existing active memory), or ``rejected`` (possible duplicate).
        """

        self._require_pending(candidate_id)
        activated = self.memory_store.activate(candidate_id)
        if activated is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        return activated

    def reject_candidate(
        self,
        candidate_id: str,
        *,
        reason: str,
    ) -> MemoryRecord:
        record = self._require_pending(candidate_id)
        source = record.source.model_copy(deep=True)
        source.extra["rejection_reason"] = reason
        updated = record.model_copy(
            update={
                "status": "rejected",
                "source": source,
                "updated_at": utc_now(),
            }
        )
        self.memory_store.update_memory(updated)
        return updated

    def retry_candidate(self, candidate_id: str) -> MemoryRecord:
        record = self.memory_store.get_memory(candidate_id)
        if record is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        if record.status not in {"rejected", "merged"}:
            raise ValueError(
                f"candidate cannot be retried from status: {record.status}"
            )

        source = record.source.model_copy(deep=True)
        for key in ("dedupe", "rejection_reason", "merged_into_candidate_id"):
            source.extra.pop(key, None)
        updated = record.model_copy(
            update={
                "status": "pending_review",
                "source": source,
                "updated_at": utc_now(),
            }
        )
        self.memory_store.update_memory(updated)
        return updated

    def merge_candidates(
        self,
        source_ids: list[str],
        merged: MemoryCreate,
    ) -> MemoryRecord:
        """Combine several pending memories into one new pending memory.

        Provenance is derived from the sources: the union of evidence event ids
        and source segment ids, plus the source ids, land in the merged memory's
        ``source.extra['merged_from']``. Each source is marked ``merged`` with
        ``merged_into_candidate_id``. The new memory is ``pending_review`` and
        stays editable before approval.
        """

        unique_ids = list(dict.fromkeys(source_ids))
        if len(unique_ids) < 2:
            raise ValueError("merge requires at least two distinct source candidates")
        sources = [self._require_pending(candidate_id) for candidate_id in unique_ids]

        evidence_event_ids = sorted(
            {eid for source in sources for eid in source.source.evidence_event_ids}
        )
        source_segment_ids = sorted(
            {
                segment_id
                for source in sources
                if (segment_id := source.source.extra.get("source_session_segment_id"))
                is not None
            }
        )

        merged_source = merged.source.model_copy(deep=True)
        merged_source.evidence_event_ids = evidence_event_ids
        merged_source.extra["merged_from"] = {
            "source_candidate_ids": unique_ids,
            "source_session_segment_ids": source_segment_ids,
        }
        merged_source.extra.pop("source_session_segment_id", None)
        new_memory = self.memory_store.create_pending(
            merged.model_copy(update={"source": merged_source})
        )

        for source in sources:
            updated_source = source.source.model_copy(deep=True)
            updated_source.extra["merged_into_candidate_id"] = new_memory.id
            self.memory_store.update_memory(
                source.model_copy(
                    update={
                        "status": "merged",
                        "source": updated_source,
                        "updated_at": utc_now(),
                    }
                )
            )
        return new_memory

    def archive_candidate(self, candidate_id: str) -> MemoryRecord:
        """Hide a candidate from the active queue while retaining it for audit."""

        record = self.memory_store.get_memory(candidate_id)
        if record is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        if record.status == "archived":
            raise ValueError("candidate is already archived")
        updated = record.model_copy(
            update={"status": "archived", "updated_at": utc_now()}
        )
        self.memory_store.update_memory(updated)
        return updated

    def _require_pending(self, candidate_id: str) -> MemoryRecord:
        record = self.memory_store.get_memory(candidate_id)
        if record is None:
            raise ValueError(f"candidate not found: {candidate_id}")
        if record.status != "pending_review":
            raise ValueError(
                f"candidate is not pending_review: {record.status}"
            )
        return record
