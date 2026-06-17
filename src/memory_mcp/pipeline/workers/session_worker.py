from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from memory_mcp.core.events import EventRecord, EventStore, SessionSegmentRecord

TERMINAL_SESSION_STATUSES = {"processed", "skipped", "failed"}
SESSION_CHECKPOINT = "session_worker"
DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class SessionWorkerResult:
    scanned_events: int
    upserted_segments: int
    idle_segments: int
    open_segments: int


class SessionWorker:
    """Group events into session segments incrementally.

    The first run with no checkpoint backfills via a full scan and records a
    cursor at the high-water mark. Subsequent runs read only events after the
    cursor, extend or split the per-session current segment, and flush the
    segment writes plus the cursor advance in one transaction per batch so an
    interrupted run replays cleanly.
    """

    def __init__(
        self,
        *,
        event_store: EventStore,
        idle_after_seconds: int = 600,
        max_segment_gap_seconds: int = 7200,
    ) -> None:
        self.event_store = event_store
        self.idle_after_seconds = idle_after_seconds
        self.max_segment_gap_seconds = max_segment_gap_seconds

    def run_once(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> SessionWorkerResult:
        current_time = now or datetime.now(timezone.utc)
        cursor = self._load_cursor()
        if cursor is None:
            return self._backfill(now=current_time)
        return self._incremental(cursor, now=current_time, batch_size=batch_size)

    # ------------------------------------------------------------------ #
    # Incremental path
    # ------------------------------------------------------------------ #
    def _incremental(
        self,
        cursor: tuple[datetime, str],
        *,
        now: datetime,
        batch_size: int,
    ) -> SessionWorkerResult:
        created_at, event_id = cursor
        scanned = 0
        upserted = 0
        idle_written = 0
        open_written = 0

        while True:
            batch = self.event_store.list_events_after(
                created_at, event_id, limit=batch_size
            )
            if not batch:
                break

            # Per-batch working copy: (project, session_id) -> open segment.
            # Seeded lazily from the DB on first touch, mutated in memory, then
            # flushed once. It does not survive the batch; the next batch
            # re-seeds from the just-committed rows.
            open_segments: dict[tuple[str, str], SessionSegmentRecord] = {}
            last = (created_at, event_id)
            for event in batch:
                key = _session_key(event)
                current = self._current_segment(open_segments, key)
                open_segments[key] = self._extend_or_split(current, event, key, now=now)
                last = (event.created_at, event.id)
                scanned += 1

            with self.event_store.transaction() as conn:
                for segment in open_segments.values():
                    self.event_store.upsert_session_segment(segment, conn=conn)
                    upserted += 1
                    if segment.status == "idle":
                        idle_written += 1
                    elif segment.status == "open":
                        open_written += 1
                self.event_store.set_checkpoint(
                    SESSION_CHECKPOINT, _dump_cursor(*last), conn=conn
                )

            created_at, event_id = last
            if len(batch) < batch_size:
                break

        # Quiescent sessions get no new events, so flip their stale open
        # segments idle with a targeted query rather than the cursor walk.
        flipped = self.event_store.mark_open_segments_idle(self._idle_threshold(now))
        return SessionWorkerResult(
            scanned_events=scanned,
            upserted_segments=upserted,
            idle_segments=idle_written + flipped,
            open_segments=open_written,
        )

    def _current_segment(
        self,
        cache: dict[tuple[str, str], SessionSegmentRecord | None],
        key: tuple[str, str],
    ) -> SessionSegmentRecord | None:
        if key in cache:
            return cache[key]
        segment = self.event_store.get_latest_segment_for_session(*key)
        cache[key] = segment
        return segment

    def _extend_or_split(
        self,
        current: SessionSegmentRecord | None,
        event: EventRecord,
        key: tuple[str, str],
        *,
        now: datetime,
    ) -> SessionSegmentRecord:
        project, session_id = key
        if current is None:
            return self._new_segment(project, session_id, 0, event, now=now)
        if current.status in TERMINAL_SESSION_STATUSES:
            # The predecessor was already extracted; do not mutate it.
            return self._new_segment(
                project, session_id, current.segment_index + 1, event, now=now
            )
        if _gap_seconds(current.last_event_at, event.created_at) > self.max_segment_gap_seconds:
            return self._new_segment(
                project, session_id, current.segment_index + 1, event, now=now
            )
        return current.model_copy(
            update={
                "last_event_at": event.created_at,
                "event_count": current.event_count + 1,
                "status": self._status_for(event.created_at, now),
            }
        )

    def _new_segment(
        self,
        project: str,
        session_id: str,
        segment_index: int,
        event: EventRecord,
        *,
        now: datetime,
    ) -> SessionSegmentRecord:
        return SessionSegmentRecord(
            id=_segment_id(project, session_id, segment_index),
            project=project,
            session_id=session_id,
            segment_index=segment_index,
            first_event_at=event.created_at,
            last_event_at=event.created_at,
            event_count=1,
            status=self._status_for(event.created_at, now),
        )

    def rebuild(self, *, now: datetime | None = None) -> SessionWorkerResult:
        """Clear non-terminal segments and replay them from a full scan.

        The repair/migration escape hatch: it discards possibly-corrupt
        non-terminal segments (for example from out-of-order events), keeps
        already-extracted terminal segments, and rederives via the full-scan
        builder, which also resets the cursor to the high-water mark.
        """

        current_time = now or datetime.now(timezone.utc)
        self.event_store.delete_non_terminal_session_segments()
        return self._backfill(now=current_time)

    # ------------------------------------------------------------------ #
    # Backfill / repair path (also the parity-test oracle)
    # ------------------------------------------------------------------ #
    def _backfill(self, *, now: datetime) -> SessionWorkerResult:
        events = self.event_store.list_events()
        segments = self._build_segments(events, now=now)
        to_write = [
            segment for segment in segments if not self._is_terminal(segment.id)
        ]
        with self.event_store.transaction() as conn:
            for segment in to_write:
                self.event_store.upsert_session_segment(segment, conn=conn)
            if events:
                last = max(events, key=lambda item: (item.created_at, item.id))
                self.event_store.set_checkpoint(
                    SESSION_CHECKPOINT,
                    _dump_cursor(last.created_at, last.id),
                    conn=conn,
                )
        return SessionWorkerResult(
            scanned_events=len(events),
            upserted_segments=len(to_write),
            idle_segments=sum(1 for s in to_write if s.status == "idle"),
            open_segments=sum(1 for s in to_write if s.status == "open"),
        )

    def _is_terminal(self, segment_id: str) -> bool:
        existing = self.event_store.get_session_segment(segment_id)
        return existing is not None and existing.status in TERMINAL_SESSION_STATUSES

    def _build_segments(
        self,
        events: list[EventRecord],
        *,
        now: datetime,
    ) -> list[SessionSegmentRecord]:
        grouped: dict[tuple[str, str], list[EventRecord]] = {}
        for event in events:
            grouped.setdefault(_session_key(event), []).append(event)

        segments: list[SessionSegmentRecord] = []
        for (project, session_id), session_events in sorted(grouped.items()):
            sorted_events = sorted(session_events, key=lambda item: (item.created_at, item.id))
            current: list[EventRecord] = []
            segment_index = 0
            for event in sorted_events:
                if current and _gap_seconds(current[-1].created_at, event.created_at) > self.max_segment_gap_seconds:
                    segments.append(
                        self._make_segment(
                            project=project,
                            session_id=session_id,
                            segment_index=segment_index,
                            events=current,
                            now=now,
                        )
                    )
                    current = []
                    segment_index += 1
                current.append(event)

            if current:
                segments.append(
                    self._make_segment(
                        project=project,
                        session_id=session_id,
                        segment_index=segment_index,
                        events=current,
                        now=now,
                    )
                )

        return segments

    def _make_segment(
        self,
        *,
        project: str,
        session_id: str,
        segment_index: int,
        events: list[EventRecord],
        now: datetime,
    ) -> SessionSegmentRecord:
        first_event_at = events[0].created_at
        last_event_at = events[-1].created_at
        return SessionSegmentRecord(
            id=_segment_id(project, session_id, segment_index),
            project=project,
            session_id=session_id,
            segment_index=segment_index,
            first_event_at=first_event_at,
            last_event_at=last_event_at,
            event_count=len(events),
            status=self._status_for(last_event_at, now),
        )

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #
    def _status_for(self, last_event_at: datetime, now: datetime) -> str:
        return "idle" if _gap_seconds(last_event_at, now) >= self.idle_after_seconds else "open"

    def _idle_threshold(self, now: datetime) -> datetime:
        return now - timedelta(seconds=self.idle_after_seconds)

    def _load_cursor(self) -> tuple[datetime, str] | None:
        raw = self.event_store.get_checkpoint(SESSION_CHECKPOINT)
        if raw is None:
            return None
        data = json.loads(raw)
        return (datetime.fromisoformat(data["created_at"]), data["id"])


def _dump_cursor(created_at: datetime, event_id: str) -> str:
    return json.dumps(
        {
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "id": event_id,
        }
    )


def _session_key(event: EventRecord) -> tuple[str, str]:
    return (
        event.project or "",
        event.session_id or event.run_id or event.source or "",
    )


def _segment_id(project: str, session_id: str, segment_index: int) -> str:
    digest = hashlib.sha256(f"{project}\0{session_id}\0{segment_index}".encode()).hexdigest()
    return f"seg_{digest[:24]}"


def _gap_seconds(start: datetime, end: datetime) -> float:
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
