from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from memory_mcp.core.events import EventRecord, EventStore
from memory_mcp.core.models import MemoryFeedback
from memory_mcp.core.store import LocalMemoryStore


@dataclass(frozen=True)
class EventWorkerResult:
    processed: int
    failed: int
    remaining: int


class EventWorker:
    def __init__(
        self,
        *,
        memory_store: LocalMemoryStore,
        event_store: EventStore,
    ) -> None:
        self.memory_store = memory_store
        self.event_store = event_store

    def run_once(self, *, limit: int = 100) -> EventWorkerResult:
        processed = 0
        failed = 0
        for event in self.event_store.list_unprocessed(limit=limit):
            try:
                self.process_event(event)
            except Exception as exc:  # pragma: no cover - defensive safety path
                failed += 1
                self.event_store.mark_failed(event.id, str(exc))
            else:
                processed += 1
                self.event_store.mark_processed(event.id)

        return EventWorkerResult(
            processed=processed,
            failed=failed,
            remaining=self.event_store.count_unprocessed(),
        )

    def process_event(self, event: EventRecord) -> None:
        if event.event_type == "memory_feedback":
            self._process_memory_feedback(event)
        elif event.event_type == "memory_retrieved":
            self._process_memory_retrieved(event)

    def _process_memory_feedback(self, event: EventRecord) -> None:
        if event.payload.get("already_applied") is True:
            return

        memory_id = _required_str(event.payload, "memory_id")
        signal = _required_str(event.payload, "signal")
        weight = float(event.payload.get("weight", 1.0))
        context = event.payload.get("context")
        if not isinstance(context, dict):
            context = {}

        updated = self.memory_store.record_feedback(
            MemoryFeedback(
                memory_id=memory_id,
                signal=signal,  # type: ignore[arg-type]
                weight=weight,
                context=context,
            )
        )
        if updated is None:
            raise ValueError(f"memory not found: {memory_id}")

    def _process_memory_retrieved(self, event: EventRecord) -> None:
        memory_ids = event.payload.get("memory_ids", [])
        if not isinstance(memory_ids, list):
            raise ValueError("memory_retrieved payload memory_ids must be a list")

        for memory_id in memory_ids:
            if not isinstance(memory_id, str):
                continue
            record = self.memory_store.get_memory(memory_id)
            if record is None:
                continue
            self.memory_store.update_memory(
                record.model_copy(
                    update={
                        "score": _clamp_score(record.score + 0.01),
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
            )


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"event payload requires string field: {key}")
    return value


def _clamp_score(score: float) -> float:
    return max(0.0, min(score, 1.0))
