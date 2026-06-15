from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from memory_mcp.core.events import EventRecord, EventStore, SessionSegmentRecord

TERMINAL_SESSION_STATUSES = {"processed", "skipped", "failed"}


@dataclass(frozen=True)
class SessionWorkerResult:
    scanned_events: int
    upserted_segments: int
    idle_segments: int
    open_segments: int


class SessionWorker:
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

    def run_once(self, *, now: datetime | None = None) -> SessionWorkerResult:
        current_time = now or datetime.now(timezone.utc)
        events = self.event_store.list_events()
        upserted = 0
        idle = 0
        open_ = 0

        for segment in self._build_segments(events, now=current_time):
            existing = self.event_store.get_session_segment(segment.id)
            if existing is not None and existing.status in TERMINAL_SESSION_STATUSES:
                continue
            self.event_store.upsert_session_segment(segment)
            upserted += 1
            if segment.status == "idle":
                idle += 1
            elif segment.status == "open":
                open_ += 1

        return SessionWorkerResult(
            scanned_events=len(events),
            upserted_segments=upserted,
            idle_segments=idle,
            open_segments=open_,
        )

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
        idle_for = _gap_seconds(last_event_at, now)
        status = "idle" if idle_for >= self.idle_after_seconds else "open"
        return SessionSegmentRecord(
            id=_segment_id(project, session_id, segment_index),
            project=project,
            session_id=session_id,
            segment_index=segment_index,
            first_event_at=first_event_at,
            last_event_at=last_event_at,
            event_count=len(events),
            status=status,
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
