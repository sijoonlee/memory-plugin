from __future__ import annotations

from datetime import date, datetime, timezone

from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.scoring import apply_daily_decay


DAILY_DECAY_CHECKPOINT = "daily_decay_date"


class DecayWorker:
    def __init__(self, *, memory_store: LocalMemoryStore) -> None:
        self.memory_store = memory_store

    def run_once(self, today: date | None = None) -> int:
        current_date = today or datetime.now(timezone.utc).date()
        last_decay = self.memory_store.get_checkpoint(DAILY_DECAY_CHECKPOINT)
        if last_decay == current_date.isoformat():
            return 0

        days = _days_since(last_decay, current_date)
        if days <= 0:
            self.memory_store.set_checkpoint(
                DAILY_DECAY_CHECKPOINT,
                current_date.isoformat(),
            )
            return 0

        decayed = 0
        for record in self.memory_store.list_memories(status="active"):
            updated = apply_daily_decay(record, days)
            if updated.score != record.score:
                self.memory_store.update_memory(updated)
                decayed += 1

        self.memory_store.set_checkpoint(
            DAILY_DECAY_CHECKPOINT,
            current_date.isoformat(),
        )
        return decayed


def _days_since(last_decay: str | None, current_date: date) -> int:
    if last_decay is None:
        return 0
    try:
        previous_date = date.fromisoformat(last_decay)
    except ValueError:
        return 0
    return max((current_date - previous_date).days, 0)
