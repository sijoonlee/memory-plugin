from __future__ import annotations

from datetime import datetime, timezone

from memory_mcp.core.models import MemoryRecord


DAILY_DECAY_FACTOR = 0.995


def apply_daily_decay(record: MemoryRecord, days: int) -> MemoryRecord:
    if days <= 0 or record.status != "active":
        return record
    decayed_score = _clamp_score(record.score * (DAILY_DECAY_FACTOR**days))
    return record.model_copy(
        update={
            "score": decayed_score,
            "updated_at": datetime.now(timezone.utc),
        }
    )


def _clamp_score(score: float) -> float:
    return max(0.0, min(score, 1.0))
