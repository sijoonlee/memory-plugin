from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


MemoryStatus = Literal["active", "stale", "superseded", "rejected", "archived"]
MemoryFeedbackSignal = Literal[
    "retrieved",
    "used",
    "helpful",
    "not_helpful",
    "incorrect",
    "stale",
    "contradicted",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemorySource(BaseModel):
    kind: str = "manual"
    session_id: str | None = None
    message_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    evidence_event_ids: list[str] = Field(default_factory=list)
    creation_reason: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class MemoryCreate(BaseModel):
    what_happened: str
    when_useful: str
    helpful_explanation: str
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = Field(default_factory=MemorySource)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    score: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryRecord(MemoryCreate):
    id: str
    content_for_embedding: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_retrieved_at: datetime | None = None
    last_used_at: datetime | None = None
    retrieval_count: int = 0
    use_count: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    status: MemoryStatus = "active"


class MemorySearchResult(BaseModel):
    memory: MemoryRecord
    semantic_similarity: float
    final_score: float
    retrieval_reason: str


class MemoryFeedback(BaseModel):
    memory_id: str
    signal: MemoryFeedbackSignal
    weight: float = Field(default=1.0, ge=0.0)
    context: dict[str, Any] = Field(default_factory=dict)
