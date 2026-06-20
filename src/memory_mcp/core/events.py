from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from contextlib import contextmanager

from pydantic import BaseModel, Field

from memory_mcp.core import checkpoints
from memory_mcp.core.redaction import redact_payload


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventCreate(BaseModel):
    event_type: str
    source: str
    project: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventRecord(EventCreate):
    id: str
    created_at: datetime
    processed_at: datetime | None = None
    failed_at: datetime | None = None
    error: str | None = None


SessionStatus = Literal["open", "idle", "processed", "skipped", "failed"]


class SessionSegmentRecord(BaseModel):
    id: str
    project: str
    session_id: str
    segment_index: int
    first_event_at: datetime
    last_event_at: datetime
    event_count: int
    status: SessionStatus
    processed_at: datetime | None = None
    error: str | None = None


class EventStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.sqlite_path = self.root / "events.sqlite"
        self.root.mkdir(parents=True, exist_ok=True)
        self._init_sqlite()

    def append_event(
        self,
        event: EventCreate,
        *,
        created_at: datetime | None = None,
    ) -> EventRecord:
        payload = event.model_dump()
        payload["payload"] = redact_payload(payload["payload"])
        record = EventRecord(
            id=f"evt_{uuid.uuid4().hex}",
            created_at=created_at or utc_now(),
            **payload,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    id, event_type, source, project, session_id, run_id,
                    payload_json, created_at, processed_at, failed_at, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.event_type,
                    record.source,
                    record.project,
                    record.session_id,
                    record.run_id,
                    json.dumps(record.payload),
                    _dt_to_text(record.created_at),
                    None,
                    None,
                    None,
                ),
            )
        return record

    def list_events(self) -> list[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY created_at, id"
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_events_after(
        self,
        created_at: datetime | None,
        event_id: str | None,
        *,
        limit: int,
    ) -> list[EventRecord]:
        """Return events strictly after the ``(created_at, id)`` cursor.

        The event id is the tie-breaker for events sharing a timestamp. A ``None``
        cursor reads from the beginning, so the same query serves both the first
        backfill and steady-state incremental runs.
        """

        if created_at is None:
            query = "SELECT * FROM events ORDER BY created_at, id LIMIT ?"
            params: tuple[Any, ...] = (limit,)
        else:
            cursor = _dt_to_text(created_at)
            query = """
                SELECT *
                FROM events
                WHERE created_at > ?
                   OR (created_at = ? AND id > ?)
                ORDER BY created_at, id
                LIMIT ?
            """
            params = (cursor, cursor, event_id or "", limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_events_for_session_segment(
        self,
        segment: SessionSegmentRecord,
    ) -> list[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM events
                WHERE COALESCE(project, '') = ?
                  AND COALESCE(session_id, run_id, source, '') = ?
                  AND created_at >= ?
                  AND created_at <= ?
                ORDER BY created_at, id
                """,
                (
                    segment.project,
                    segment.session_id,
                    _dt_to_text(segment.first_event_at),
                    _dt_to_text(segment.last_event_at),
                ),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def list_events_by_ids(self, event_ids: list[str]) -> list[EventRecord]:
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM events
                WHERE id IN ({placeholders})
                """,
                tuple(event_ids),
            ).fetchall()
        events = [_row_to_event(row) for row in rows]
        events_by_id = {event.id: event for event in events}
        return [events_by_id[event_id] for event_id in event_ids if event_id in events_by_id]

    def list_unprocessed(self, limit: int = 100) -> list[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM events
                WHERE processed_at IS NULL AND failed_at IS NULL
                ORDER BY created_at, id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def count_unprocessed(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM events
                WHERE processed_at IS NULL AND failed_at IS NULL
                """
            ).fetchone()
        return int(row["count"])

    def mark_processed(self, event_id: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events
                SET processed_at = ?, failed_at = NULL, error = NULL
                WHERE id = ?
                """,
                (_dt_to_text(now), event_id),
            )

    def mark_failed(self, event_id: str, error: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE events
                SET failed_at = ?, error = ?
                WHERE id = ?
                """,
                (_dt_to_text(now), error, event_id),
            )

    def count_failed(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM events
                WHERE failed_at IS NOT NULL
                """
            ).fetchone()
        return int(row["count"])

    def upsert_session_segment(
        self,
        segment: SessionSegmentRecord,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if conn is not None:
            self._upsert_session_segment(conn, segment)
            return
        with self._connect() as own:
            self._upsert_session_segment(own, segment)

    def _upsert_session_segment(
        self,
        conn: sqlite3.Connection,
        segment: SessionSegmentRecord,
    ) -> None:
        existing = conn.execute(
            "SELECT status FROM session_segments WHERE id = ?",
            (segment.id,),
        ).fetchone()
        status = segment.status
        if existing is not None and existing["status"] in {
            "processed",
            "skipped",
            "failed",
        }:
            status = existing["status"]
        conn.execute(
            """
            INSERT INTO session_segments (
                id, project, session_id, segment_index, first_event_at,
                last_event_at, event_count, status, processed_at, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                first_event_at = excluded.first_event_at,
                last_event_at = excluded.last_event_at,
                event_count = excluded.event_count,
                status = excluded.status
            """,
            (
                segment.id,
                segment.project,
                segment.session_id,
                segment.segment_index,
                _dt_to_text(segment.first_event_at),
                _dt_to_text(segment.last_event_at),
                segment.event_count,
                status,
                _optional_dt_to_text(segment.processed_at),
                segment.error,
            ),
        )

    def list_session_segments(
        self,
        *,
        status: str | None = None,
    ) -> list[SessionSegmentRecord]:
        query = "SELECT * FROM session_segments"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY first_event_at, id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_session_segment(row) for row in rows]

    def get_session_segment(self, segment_id: str) -> SessionSegmentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_segments WHERE id = ?",
                (segment_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_session_segment(row)

    def get_latest_segment_for_session(
        self,
        project: str,
        session_id: str,
    ) -> SessionSegmentRecord | None:
        """Return the highest-index segment for a session, or ``None``.

        Used by the incremental worker to find the open segment a new event
        should extend or split from, via ``idx_session_segments_session``.
        """

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM session_segments
                WHERE project = ? AND session_id = ?
                ORDER BY segment_index DESC
                LIMIT 1
                """,
                (project, session_id),
            ).fetchone()
        if row is None:
            return None
        return _row_to_session_segment(row)

    def mark_open_segments_idle(
        self,
        threshold: datetime,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """Flip ``open`` segments whose last event is at or before ``threshold``.

        A targeted UPDATE backed by ``idx_session_segments_status``; it never
        reads event payloads. Returns the number of segments flipped to idle.
        """

        sql = """
            UPDATE session_segments
            SET status = 'idle'
            WHERE status = 'open' AND last_event_at <= ?
        """
        param = _dt_to_text(threshold)
        if conn is not None:
            return conn.execute(sql, (param,)).rowcount
        with self._connect() as own:
            return own.execute(sql, (param,)).rowcount

    def delete_non_terminal_session_segments(self) -> int:
        """Delete every non-terminal segment, keeping extracted ones for audit.

        Used by the rebuild/repair path before replaying segments from scratch.
        Returns the number of segments deleted.
        """

        with self._connect() as conn:
            return conn.execute(
                """
                DELETE FROM session_segments
                WHERE status NOT IN ('processed', 'skipped', 'failed')
                """
            ).rowcount

    def mark_session_segment_status(
        self,
        segment_id: str,
        status: SessionStatus,
        *,
        error: str | None = None,
    ) -> None:
        processed_at = utc_now() if status in {"processed", "skipped", "failed"} else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE session_segments
                SET status = ?, processed_at = ?, error = ?
                WHERE id = ?
                """,
                (
                    status,
                    _optional_dt_to_text(processed_at),
                    error,
                    segment_id,
                ),
            )

    def _init_sqlite(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    project TEXT,
                    session_id TEXT,
                    run_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT,
                    failed_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_unprocessed
                ON events(processed_at, failed_at, created_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_session
                ON events(project, session_id, run_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_created_id
                ON events(created_at, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_segments (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL,
                    first_event_at TEXT NOT NULL,
                    last_event_at TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    processed_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_segments_status
                ON session_segments(status, last_event_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_segments_session
                ON session_segments(project, session_id, segment_index)
                """
            )
            checkpoints.create_checkpoints_table(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield one connection whose writes commit (or roll back) atomically.

        Used by the incremental session worker so segment writes and the cursor
        advance land in a single transaction; an interrupted run replays cleanly.
        """

        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get_checkpoint(self, name: str) -> str | None:
        with self._connect() as conn:
            return checkpoints.get_checkpoint(conn, name)

    def set_checkpoint(
        self,
        name: str,
        value: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if conn is not None:
            checkpoints.set_checkpoint(conn, name, value)
            return
        with self._connect() as own:
            checkpoints.set_checkpoint(own, name, value)

def _row_to_event(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        id=row["id"],
        event_type=row["event_type"],
        source=row["source"],
        project=row["project"],
        session_id=row["session_id"],
        run_id=row["run_id"],
        payload=json.loads(row["payload_json"]),
        created_at=_text_to_dt(row["created_at"]),
        processed_at=_optional_text_to_dt(row["processed_at"]),
        failed_at=_optional_text_to_dt(row["failed_at"]),
        error=row["error"],
    )


def _row_to_session_segment(row: sqlite3.Row) -> SessionSegmentRecord:
    return SessionSegmentRecord(
        id=row["id"],
        project=row["project"],
        session_id=row["session_id"],
        segment_index=row["segment_index"],
        first_event_at=_text_to_dt(row["first_event_at"]),
        last_event_at=_text_to_dt(row["last_event_at"]),
        event_count=row["event_count"],
        status=row["status"],
        processed_at=_optional_text_to_dt(row["processed_at"]),
        error=row["error"],
    )


def _dt_to_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _optional_dt_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _dt_to_text(value)


def _text_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_text_to_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _text_to_dt(value)
