from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from memory_mcp.core.events import EventStore
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.daemon.workers.decay_worker import (
    DAILY_DECAY_CHECKPOINT,
    DecayWorker,
)
from memory_mcp.daemon.workers.event_worker import EventWorker
from memory_mcp.daemon.workers.session_worker import SessionWorker


@dataclass(frozen=True)
class DaemonRunResult:
    processed: int
    failed: int
    sessions_upserted: int
    idle_sessions: int
    decayed: int
    remaining: int


class MemoryDaemon:
    def __init__(
        self,
        *,
        memory_store: LocalMemoryStore,
        event_store: EventStore,
    ) -> None:
        self.memory_store = memory_store
        self.event_store = event_store
        self.event_worker = EventWorker(
            memory_store=memory_store,
            event_store=event_store,
        )
        self.session_worker = SessionWorker(event_store=event_store)
        self.decay_worker = DecayWorker(memory_store=memory_store)

    def run_once(
        self,
        *,
        limit: int = 100,
        apply_decay: bool = True,
    ) -> DaemonRunResult:
        event_result = self.event_worker.run_once(limit=limit)
        session_result = self.session_worker.run_once()
        decayed = self.apply_daily_decay() if apply_decay else 0
        return DaemonRunResult(
            processed=event_result.processed,
            failed=event_result.failed,
            sessions_upserted=session_result.upserted_segments,
            idle_sessions=session_result.idle_segments,
            decayed=decayed,
            remaining=event_result.remaining,
        )

    def apply_daily_decay(self, today: date | None = None) -> int:
        return self.decay_worker.run_once(today=today)
