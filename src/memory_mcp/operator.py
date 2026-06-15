from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from memory_mcp.core.events import EventStore
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.extractors import MemoryExtractor
from memory_mcp.pipeline.workers.decay_worker import DecayWorker
from memory_mcp.pipeline.workers.event_worker import EventWorker
from memory_mcp.pipeline.workers.extraction_worker import ExtractionWorker
from memory_mcp.pipeline.workers.session_worker import SessionWorker


MEMORY_STATUSES = ["active", "stale", "superseded", "invalid", "rejected", "archived"]
SESSION_STATUSES = ["open", "idle", "processed", "skipped", "failed"]
CANDIDATE_STATUSES = ["pending_review", "approved", "rejected", "merged"]


@dataclass(frozen=True)
class OperatorStatus:
    root: str
    events: dict[str, int]
    sessions: dict[str, int]
    candidates: dict[str, int]
    memories: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OperatorProcessResult:
    events: dict[str, int]
    sessions: dict[str, int]
    extraction: dict[str, int]
    decay: dict[str, int]
    status: OperatorStatus

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.to_dict()
        return payload


class OperatorWorkflow:
    def __init__(
        self,
        *,
        root: Path | str,
        memory_store: LocalMemoryStore | None = None,
        event_store: EventStore | None = None,
    ) -> None:
        self.root = Path(root)
        self.event_store = event_store or EventStore(self.root)
        self.memory_store = memory_store or LocalMemoryStore(
            self.root,
            _NoopEmbedder(),
        )

    def status(self) -> OperatorStatus:
        return OperatorStatus(
            root=str(self.root),
            events={
                "unprocessed": self.event_store.count_unprocessed(),
                "failed": self.event_store.count_failed(),
                "total": len(self.event_store.list_events()),
            },
            sessions={
                status: len(self.event_store.list_session_segments(status=status))
                for status in SESSION_STATUSES
            },
            candidates={
                status: len(self.event_store.list_memory_candidates(status=status))
                for status in CANDIDATE_STATUSES
            },
            memories={
                status: len(self.memory_store.list_memories(status=status))
                for status in MEMORY_STATUSES
            },
        )

    def process(
        self,
        *,
        extractor: MemoryExtractor | None = None,
        event_limit: int = 100,
        extraction_limit: int = 1,
        idle_after_seconds: int = 600,
        max_segment_gap_seconds: int = 7200,
        apply_decay: bool = True,
    ) -> OperatorProcessResult:
        event_result = EventWorker(
            memory_store=self.memory_store,
            event_store=self.event_store,
        ).run_once(limit=event_limit)
        session_result = SessionWorker(
            event_store=self.event_store,
            idle_after_seconds=idle_after_seconds,
            max_segment_gap_seconds=max_segment_gap_seconds,
        ).run_once()
        if extractor is None or extraction_limit <= 0:
            extraction = {
                "processed_segments": 0,
                "skipped_segments": 0,
                "failed_segments": 0,
                "created_candidates": 0,
                "remaining_idle_segments": len(
                    self.event_store.list_session_segments(status="idle")
                ),
            }
        else:
            extraction_result = ExtractionWorker(
                event_store=self.event_store,
                extractor=extractor,
            ).run_once(limit=extraction_limit)
            extraction = asdict(extraction_result)

        decayed = (
            DecayWorker(memory_store=self.memory_store).run_once()
            if apply_decay
            else 0
        )
        return OperatorProcessResult(
            events=asdict(event_result),
            sessions=asdict(session_result),
            extraction=extraction,
            decay={"decayed": decayed},
            status=self.status(),
        )


class _NoopEmbedder:
    def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("operator workflow does not embed text")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("operator workflow does not embed text")
