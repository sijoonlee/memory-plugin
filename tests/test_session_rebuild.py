from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory_mcp.core.events import EventCreate, EventStore, SessionSegmentRecord
from memory_mcp.operator import OperatorWorkflow
from memory_mcp.pipeline.workers.session_worker import SessionWorker

BASE = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
NOW = BASE + timedelta(hours=6)


def _worker(store: EventStore) -> SessionWorker:
    return SessionWorker(
        event_store=store,
        idle_after_seconds=600,
        max_segment_gap_seconds=7200,
    )


def _append(store: EventStore, *, offset_seconds: float) -> None:
    store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"offset": offset_seconds},
        ),
        created_at=BASE + timedelta(seconds=offset_seconds),
    )


def _populate_two_segments(store: EventStore) -> None:
    _append(store, offset_seconds=0)
    _append(store, offset_seconds=60)
    _append(store, offset_seconds=3 * 60 * 60)  # gap split -> segment 1
    _worker(store).run_once(now=NOW)


def test_rebuild_clears_non_terminal_and_preserves_terminal(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _populate_two_segments(store)
    by_index = {s.segment_index: s for s in store.list_session_segments()}
    store.mark_session_segment_status(by_index[1].id, "processed")

    result = _worker(store).rebuild(now=NOW)

    rebuilt = {s.segment_index: s for s in store.list_session_segments()}
    assert rebuilt[1].status == "processed"  # terminal preserved, untouched
    assert rebuilt[0].status == "idle"  # non-terminal rederived
    assert rebuilt[0].event_count == 2
    assert result.scanned_events == 3
    assert result.upserted_segments == 1  # only the non-terminal segment rewritten


def test_rebuild_resets_cursor_so_next_run_reads_nothing(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _populate_two_segments(store)

    _worker(store).rebuild(now=NOW)

    # The cursor was reset to the high-water mark, so a follow-up run is a no-op.
    result = _worker(store).run_once(now=NOW)
    assert result.scanned_events == 0


def test_operator_rebuild_sessions_reports_status(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _populate_two_segments(store)

    payload = OperatorWorkflow(root=tmp_path, event_store=store).rebuild_sessions()

    assert payload["sessions"]["upserted_segments"] == 2
    assert payload["status"]["sessions"]["idle"] == 2


def test_mark_open_segments_idle_uses_only_segment_rows(tmp_path) -> None:
    # No events exist in the table at all: idle marking must depend solely on
    # session_segments.last_event_at, never on event payloads.
    store = EventStore(tmp_path / "events")
    store.upsert_session_segment(
        SessionSegmentRecord(
            id="seg_quiescent",
            project="/repo",
            session_id="session-1",
            segment_index=0,
            first_event_at=BASE,
            last_event_at=BASE,
            event_count=1,
            status="open",
        )
    )

    flipped = store.mark_open_segments_idle(BASE + timedelta(seconds=601))

    assert flipped == 1
    segment = store.get_session_segment("seg_quiescent")
    assert segment is not None
    assert segment.status == "idle"
