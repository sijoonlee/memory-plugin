from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_mcp.content import build_content_for_embedding
from memory_mcp.embeddings import Embedder
from memory_mcp.models import (
    MemoryCreate,
    MemoryFeedback,
    MemoryFeedbackSignal,
    MemoryRecord,
    MemorySearchResult,
)


class LocalMemoryStore:
    def __init__(self, root: Path | str, embedder: Embedder) -> None:
        self.root = Path(root)
        self.embedder = embedder
        self.lancedb_dir = self.root / "lancedb"
        self.sqlite_path = self.root / "memory.sqlite"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lancedb_dir.mkdir(parents=True, exist_ok=True)
        self._init_sqlite()

    def create_memory(self, memory: MemoryCreate) -> MemoryRecord:
        record = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex}",
            content_for_embedding=build_content_for_embedding(
                what_happened=memory.what_happened,
                when_useful=memory.when_useful,
                helpful_explanation=memory.helpful_explanation,
                tags=memory.tags,
            ),
            **memory.model_dump(),
        )
        vector = self.embedder.embed_text(record.content_for_embedding)
        with self._connect_sqlite() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, record_json, content_for_embedding, tags_json, score,
                    confidence, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.model_dump_json(),
                    record.content_for_embedding,
                    json.dumps(record.tags),
                    record.score,
                    record.confidence,
                    record.status,
                    _dt_to_text(record.created_at),
                    _dt_to_text(record.updated_at),
                ),
            )
        self._add_vector(record, vector)
        return record

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self._connect_sqlite() as conn:
            row = conn.execute(
                "SELECT record_json FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        return MemoryRecord.model_validate_json(row["record_json"])

    def search_memories(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
        min_score: float = 0.0,
    ) -> list[MemorySearchResult]:
        if limit <= 0 or not self._vector_table_exists():
            return []

        query_vector = self.embedder.embed_text(query)
        table = self._open_vector_table()
        raw_results = table.search(query_vector).limit(max(limit * 4, limit)).to_list()

        results: list[MemorySearchResult] = []
        now = datetime.now(timezone.utc)
        for raw in raw_results:
            memory_id = raw["id"]
            record = self.get_memory(memory_id)
            if record is None or record.status != "active":
                continue
            if tags and not set(tags).issubset(set(record.tags)):
                continue
            if record.score < min_score:
                continue

            semantic_similarity = _distance_to_similarity(raw.get("_distance", 0.0))
            final_score = _rank_memory(record, semantic_similarity, now)
            results.append(
                MemorySearchResult(
                    memory=record,
                    semantic_similarity=semantic_similarity,
                    final_score=final_score,
                    retrieval_reason=(
                        "Matched query against memory embedding"
                        if not tags
                        else "Matched query against memory embedding and requested tags"
                    ),
                )
            )
            if len(results) >= limit:
                break

        self._record_retrievals([result.memory.id for result in results])
        return sorted(results, key=lambda result: result.final_score, reverse=True)

    def export_jsonl(self, path: Path | str) -> int:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with self._connect_sqlite() as conn, output_path.open("w", encoding="utf-8") as file:
            rows = conn.execute("SELECT record_json FROM memories ORDER BY created_at, id")
            for row in rows:
                file.write(row["record_json"])
                file.write("\n")
                count += 1
        return count

    def record_feedback(self, feedback: MemoryFeedback) -> MemoryRecord | None:
        record = self.get_memory(feedback.memory_id)
        if record is None:
            return None

        now = datetime.now(timezone.utc)
        score_delta = _feedback_score_delta(feedback.signal) * feedback.weight
        update: dict[str, Any] = {
            "score": _clamp_score(record.score + score_delta),
            "updated_at": now,
        }

        if feedback.signal == "retrieved":
            update["last_retrieved_at"] = now
            update["retrieval_count"] = record.retrieval_count + 1
        elif feedback.signal == "used":
            update["last_used_at"] = now
            update["use_count"] = record.use_count + 1
        elif feedback.signal == "helpful":
            update["positive_feedback_count"] = record.positive_feedback_count + 1
        elif feedback.signal in {"not_helpful", "incorrect", "stale", "contradicted"}:
            update["negative_feedback_count"] = record.negative_feedback_count + 1

        if feedback.signal == "stale":
            update["status"] = "stale"
        elif feedback.signal == "contradicted":
            update["status"] = "superseded"

        updated = record.model_copy(update=update)
        with self._connect_sqlite() as conn:
            self._write_memory_record(conn, updated)
            conn.execute(
                """
                INSERT INTO feedback_events (
                    id, memory_id, signal, weight, context_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"fb_{uuid.uuid4().hex}",
                    feedback.memory_id,
                    feedback.signal,
                    feedback.weight,
                    json.dumps(feedback.context),
                    _dt_to_text(now),
                ),
            )
        return updated

    def _init_sqlite(self) -> None:
        with self._connect_sqlite() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    record_json TEXT NOT NULL,
                    content_for_embedding TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_events (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    weight REAL NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feedback_events_memory_id
                ON feedback_events(memory_id)
                """
            )

    def _connect_sqlite(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _connect_lancedb(self) -> Any:
        import lancedb

        return lancedb.connect(str(self.lancedb_dir))

    def _vector_table_exists(self) -> bool:
        db = self._connect_lancedb()
        return "memories" in self._table_names(db)

    def _open_vector_table(self) -> Any:
        return self._connect_lancedb().open_table("memories")

    def _add_vector(self, record: MemoryRecord, vector: list[float]) -> None:
        row = {
            "id": record.id,
            "vector": vector,
            "content_for_embedding": record.content_for_embedding,
            "tags": record.tags,
            "score": record.score,
            "confidence": record.confidence,
            "status": record.status,
        }
        db = self._connect_lancedb()
        if "memories" not in self._table_names(db):
            db.create_table("memories", data=[row])
            return
        db.open_table("memories").add([row])

    def _table_names(self, db: Any) -> list[str]:
        if hasattr(db, "list_tables"):
            tables = db.list_tables()
            if hasattr(tables, "tables"):
                return list(tables.tables)
            return list(tables)
        return list(db.table_names())

    def _record_retrievals(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        retrieved_at = datetime.now(timezone.utc)
        with self._connect_sqlite() as conn:
            for memory_id in memory_ids:
                record = self.get_memory(memory_id)
                if record is None:
                    continue
                updated = record.model_copy(
                    update={
                        "last_retrieved_at": retrieved_at,
                        "retrieval_count": record.retrieval_count + 1,
                        "updated_at": retrieved_at,
                    }
                )
                conn.execute(
                    self._memory_update_sql(),
                    self._memory_update_params(updated),
                )

    def _write_memory_record(
        self,
        conn: sqlite3.Connection,
        record: MemoryRecord,
    ) -> None:
        conn.execute(self._memory_update_sql(), self._memory_update_params(record))

    def _memory_update_sql(self) -> str:
        return """
        UPDATE memories
        SET record_json = ?,
            tags_json = ?,
            score = ?,
            confidence = ?,
            status = ?,
            updated_at = ?
        WHERE id = ?
        """

    def _memory_update_params(self, record: MemoryRecord) -> tuple[Any, ...]:
        return (
            record.model_dump_json(),
            json.dumps(record.tags),
            record.score,
            record.confidence,
            record.status,
            _dt_to_text(record.updated_at),
            record.id,
        )


def _distance_to_similarity(distance: float) -> float:
    return 1.0 / (1.0 + max(distance, 0.0))


def _rank_memory(
    record: MemoryRecord,
    semantic_similarity: float,
    now: datetime,
) -> float:
    recency_score = _recency_score(record.updated_at, now)
    return (
        semantic_similarity * 0.55
        + record.score * 0.25
        + recency_score * 0.10
        + record.confidence * 0.10
    )


def _recency_score(updated_at: datetime, now: datetime) -> float:
    age_days = max((now - updated_at).total_seconds() / 86400.0, 0.0)
    return max(0.0, 1.0 - min(age_days / 365.0, 1.0))


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _feedback_score_delta(signal: MemoryFeedbackSignal) -> float:
    return {
        "retrieved": 0.01,
        "used": 0.10,
        "helpful": 0.25,
        "not_helpful": -0.20,
        "incorrect": -0.50,
        "stale": -0.30,
        "contradicted": -0.60,
    }[signal]


def _clamp_score(score: float) -> float:
    return max(0.0, min(score, 1.0))
