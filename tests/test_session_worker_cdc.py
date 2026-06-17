from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memory_mcp.core.events import EventCreate, EventStore
from memory_mcp.pipeline.workers.session_worker import SessionWorker

BASE = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
NOW = BASE + timedelta(hours=6)


def _worker(store: EventStore) -> SessionWorker:
    return SessionWorker(
        event_store=store,
        idle_after_seconds=600,
        max_segment_gap_seconds=7200,
    )


def _append(store: EventStore, *, session: str, offset_seconds: float) -> None:
    store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id=session,
            payload={"offset": offset_seconds},
        ),
        created_at=BASE + timedelta(seconds=offset_seconds),
    )


# Two sessions, with a >2h gap inside session-1 that must split into two segments.
STREAM = [
    ("session-1", 0),
    ("session-2", 30),
    ("session-1", 60),
    ("session-1", 3 * 60 * 60),       # gap split
    ("session-2", 3 * 60 * 60 + 30),  # gap split
    ("session-1", 3 * 60 * 60 + 90),
]


def _segments_snapshot(store: EventStore):
    return [
        (s.id, s.segment_index, s.event_count, s.first_event_at, s.last_event_at, s.status)
        for s in store.list_session_segments()
    ]


def test_incremental_matches_full_backfill(tmp_path) -> None:
    # Store A: everything present, single backfill run.
    store_a = EventStore(tmp_path / "a")
    for session, offset in STREAM:
        _append(store_a, session=session, offset_seconds=offset)
    _worker(store_a).run_once(now=NOW)

    # Store B: first wave backfilled, second wave processed incrementally.
    store_b = EventStore(tmp_path / "b")
    for session, offset in STREAM[:3]:
        _append(store_b, session=session, offset_seconds=offset)
    _worker(store_b).run_once(now=BASE + timedelta(minutes=2))
    for session, offset in STREAM[3:]:
        _append(store_b, session=session, offset_seconds=offset)
    _worker(store_b).run_once(now=NOW)

    assert _segments_snapshot(store_b) == _segments_snapshot(store_a)


def test_second_run_reads_only_new_events(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    for session, offset in STREAM:
        _append(store, session=session, offset_seconds=offset)
    _worker(store).run_once(now=NOW)

    # No new events appended; the incremental run should scan nothing.
    result = _worker(store).run_once(now=NOW)
    assert result.scanned_events == 0


def test_long_gap_creates_new_segment_incrementally(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _append(store, session="session-1", offset_seconds=0)
    _worker(store).run_once(now=BASE + timedelta(minutes=2))

    # A later event well past the max gap must open a second segment.
    _append(store, session="session-1", offset_seconds=3 * 60 * 60)
    _worker(store).run_once(now=NOW)

    segments = store.list_session_segments()
    assert [s.segment_index for s in segments] == [0, 1]
    assert [s.event_count for s in segments] == [1, 1]


def test_late_event_after_terminal_segment_opens_new_segment(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _append(store, session="session-1", offset_seconds=0)
    _append(store, session="session-1", offset_seconds=60)
    _worker(store).run_once(now=NOW)

    segment0 = store.list_session_segments()[0]
    store.mark_session_segment_status(segment0.id, "processed")

    # A new event arrives after the segment was already extracted.
    _append(store, session="session-1", offset_seconds=120)
    _worker(store).run_once(now=NOW)

    segments = store.list_session_segments()
    by_index = {s.segment_index: s for s in segments}
    assert by_index[0].status == "processed"
    assert by_index[0].event_count == 2  # untouched
    assert by_index[1].event_count == 1  # the late event


def test_resume_after_interrupt_does_not_double_count(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _append(store, session="session-1", offset_seconds=0)
    _worker(store).run_once(now=BASE + timedelta(minutes=2))

    _append(store, session="session-1", offset_seconds=60)
    _append(store, session="session-1", offset_seconds=120)

    # Interrupt the flush: the cursor advance raises, rolling back the whole
    # transaction (segments + checkpoint) for that batch.
    original = store.set_checkpoint

    def boom(*args, **kwargs):
        raise RuntimeError("interrupted before commit")

    store.set_checkpoint = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        _worker(store).run_once(now=NOW)
    store.set_checkpoint = original  # type: ignore[method-assign]

    # Nothing from the interrupted batch should have been committed.
    segment = store.list_session_segments()[0]
    assert segment.event_count == 1

    # Re-running replays the same events exactly once.
    _worker(store).run_once(now=NOW)
    segment = store.list_session_segments()[0]
    assert segment.event_count == 3
