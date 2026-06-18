from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory_mcp.core import checkpoints
from memory_mcp.core.content import build_content_for_embedding
from memory_mcp.core.embeddings import Embedder
from memory_mcp.core.models import (
    MemoryCreate,
    MemoryFeedback,
    MemoryFeedbackSignal,
    MemoryRecord,
    MemorySearchResult,
)
from memory_mcp.core.redaction import redact_text

CLEAR_DUPLICATE_SEMANTIC_THRESHOLD = 0.92
POSSIBLE_DUPLICATE_SEMANTIC_THRESHOLD = 0.82


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
        memory = _redact_memory_create(memory)
        content_for_embedding = build_content_for_embedding(
            what_happened=memory.what_happened,
            when_useful=memory.when_useful,
            helpful_explanation=memory.helpful_explanation,
            tags=memory.tags,
        )
        vector = self.embedder.embed_text(content_for_embedding)
        dedupe_match = self._find_dedupe_match(memory, vector)
        if dedupe_match is not None:
            matched_record, decision, similarity = dedupe_match
            if decision == "duplicate":
                return self._merge_duplicate_memory(
                    existing=matched_record,
                    candidate=memory,
                    similarity=similarity,
                )
            if decision == "possible_duplicate":
                return self._create_rejected_duplicate_candidate(
                    memory=memory,
                    content_for_embedding=content_for_embedding,
                    vector=vector,
                    existing=matched_record,
                    similarity=similarity,
                )

        record = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex}",
            content_for_embedding=content_for_embedding,
            **memory.model_dump(),
        )
        self._insert_memory_record(record)
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

    def delete_memory(self, memory_id: str) -> bool:
        """Permanently remove a memory from both the metadata and vector stores.

        This is a hard delete: it bypasses the ``stale`` / ``superseded`` /
        ``invalid`` audit statuses, so prefer ``record_feedback`` for normal
        lifecycle changes and reserve delete for secret removal or explicit user
        requests. ``feedback_events`` rows are intentionally left for audit.
        Returns ``True`` when a memory was deleted, ``False`` for an unknown id.
        """

        with self._connect_sqlite() as conn:
            deleted = (
                conn.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (memory_id,),
                ).rowcount
                > 0
            )
        if deleted:
            self._delete_vector(memory_id)
        return deleted

    def search_memories(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
        min_score: float = 0.0,
        project: str | None = None,
    ) -> list[MemorySearchResult]:
        """Semantic search over active memories.

        When ``project`` is given, scoping is *inclusive*: the repo's own
        memories and global (project-less) memories are returned, but other
        repos' memories are excluded. ``project=None`` searches everything.
        """

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
            if project is not None and record.project not in (project, None):
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

    def list_memories(
        self,
        *,
        status: str | None = None,
        project: str | None = None,
    ) -> list[MemoryRecord]:
        query = "SELECT record_json FROM memories"
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at, id"
        with self._connect_sqlite() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [MemoryRecord.model_validate_json(row["record_json"]) for row in rows]

    def update_memory(self, record: MemoryRecord) -> None:
        with self._connect_sqlite() as conn:
            self._write_memory_record(conn, record)

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
            update["status"] = (
                "superseded"
                if feedback.context.get("replacement_memory_id")
                else "stale"
            )
        elif feedback.signal == "incorrect":
            update["status"] = "invalid"

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
            self._ensure_memories_project_column(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project)"
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
            checkpoints.create_checkpoints_table(conn)

    def _ensure_memories_project_column(self, conn: sqlite3.Connection) -> None:
        """Add the denormalized ``project`` column to pre-M17 stores in place."""

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
        if "project" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN project TEXT")

    def get_checkpoint(self, name: str) -> str | None:
        with self._connect_sqlite() as conn:
            return checkpoints.get_checkpoint(conn, name)

    def set_checkpoint(self, name: str, value: str) -> None:
        with self._connect_sqlite() as conn:
            checkpoints.set_checkpoint(conn, name, value)

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

    def _delete_vector(self, memory_id: str) -> None:
        db = self._connect_lancedb()
        if "memories" not in self._table_names(db):
            return
        db.open_table("memories").delete(f"id = '{memory_id}'")

    def _insert_memory_record(self, record: MemoryRecord) -> None:
        with self._connect_sqlite() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, record_json, content_for_embedding, tags_json, score,
                    confidence, status, project, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.model_dump_json(),
                    record.content_for_embedding,
                    json.dumps(record.tags),
                    record.score,
                    record.confidence,
                    record.status,
                    record.project,
                    _dt_to_text(record.created_at),
                    _dt_to_text(record.updated_at),
                ),
            )

    def _find_dedupe_match(
        self,
        candidate: MemoryCreate,
        candidate_vector: list[float],
    ) -> tuple[MemoryRecord, str, float] | None:
        if not self._vector_table_exists():
            return None

        table = self._open_vector_table()
        raw_results = table.search(candidate_vector).limit(5).to_list()
        best_possible: tuple[MemoryRecord, str, float] | None = None
        for raw in raw_results:
            record = self.get_memory(raw["id"])
            if record is None or record.status != "active":
                continue

            semantic_similarity = _distance_to_similarity(raw.get("_distance", 0.0))
            field_match = _dedupe_field_match(candidate, record)
            if (
                semantic_similarity >= CLEAR_DUPLICATE_SEMANTIC_THRESHOLD
                and field_match == "duplicate"
            ):
                return (record, "duplicate", semantic_similarity)
            if (
                semantic_similarity >= POSSIBLE_DUPLICATE_SEMANTIC_THRESHOLD
                and field_match == "possible_duplicate"
                and best_possible is None
            ):
                best_possible = (record, "possible_duplicate", semantic_similarity)

        return best_possible

    def _merge_duplicate_memory(
        self,
        *,
        existing: MemoryRecord,
        candidate: MemoryCreate,
        similarity: float,
    ) -> MemoryRecord:
        now = datetime.now(timezone.utc)
        source = existing.source.model_copy(deep=True)
        dedupe = dict(source.extra.get("dedupe", {}))
        dedupe["decision"] = "merged_duplicate"
        dedupe["duplicate_count"] = int(dedupe.get("duplicate_count", 0)) + 1
        dedupe["last_similarity"] = similarity
        dedupe["last_candidate"] = {
            "what_happened": candidate.what_happened,
            "when_useful": candidate.when_useful,
            "helpful_explanation": candidate.helpful_explanation,
            "tags": candidate.tags,
            "source": candidate.source.model_dump(mode="json"),
        }
        source.extra["dedupe"] = dedupe

        updated = existing.model_copy(
            update={
                "tags": sorted(set(existing.tags).union(candidate.tags)),
                "confidence": max(existing.confidence, candidate.confidence),
                "score": max(existing.score, candidate.score),
                "source": source,
                "updated_at": now,
            }
        )
        self.update_memory(updated)
        return updated

    def _create_rejected_duplicate_candidate(
        self,
        *,
        memory: MemoryCreate,
        content_for_embedding: str,
        vector: list[float],
        existing: MemoryRecord,
        similarity: float,
    ) -> MemoryRecord:
        source = memory.source.model_copy(deep=True)
        source.extra["dedupe"] = {
            "decision": "possible_duplicate_rejected",
            "existing_memory_id": existing.id,
            "similarity": similarity,
            "reason": "Candidate is similar to an existing active memory.",
        }
        record = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex}",
            content_for_embedding=content_for_embedding,
            status="rejected",
            source=source,
            **memory.model_dump(exclude={"source"}),
        )
        self._insert_memory_record(record)
        self._add_vector(record, vector)
        return record

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


def _redact_memory_create(memory: MemoryCreate) -> MemoryCreate:
    return memory.model_copy(
        update={
            "what_happened": redact_text(memory.what_happened),
            "when_useful": redact_text(memory.when_useful),
            "helpful_explanation": redact_text(memory.helpful_explanation),
            "tags": [redact_text(tag) for tag in memory.tags],
        }
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


def _dedupe_field_match(candidate: MemoryCreate, existing: MemoryRecord) -> str:
    lesson_similarity = _token_jaccard(
        candidate.what_happened,
        existing.what_happened,
    )
    situation_similarity = _token_jaccard(candidate.when_useful, existing.when_useful)
    action_similarity = _token_jaccard(
        candidate.helpful_explanation,
        existing.helpful_explanation,
    )
    tag_overlap = bool(set(candidate.tags).intersection(existing.tags))

    if (
        lesson_similarity >= 0.70
        and situation_similarity >= 0.80
        and action_similarity >= 0.80
    ):
        return "duplicate"
    if (
        lesson_similarity >= 0.35
        and situation_similarity >= 0.60
        and (action_similarity >= 0.60 or tag_overlap)
    ):
        return "possible_duplicate"
    return "distinct"


def _token_jaccard(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / len(
        left_tokens.union(right_tokens)
    )


def _tokens(text: str) -> set[str]:
    normalized = []
    for char in text.lower():
        normalized.append(char if char.isalnum() else " ")
    stop_words = {"a", "an", "and", "or", "the", "to", "of", "in", "this"}
    return {
        token
        for token in "".join(normalized).split()
        if token and token not in stop_words
    }
