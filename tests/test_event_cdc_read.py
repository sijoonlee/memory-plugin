from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory_mcp.core.events import EventCreate, EventStore, SessionSegmentRecord

BASE = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _append(store: EventStore, *, offset_seconds: float) -> str:
    record = store.append_event(
        EventCreate(event_type="user_prompt", source="test", payload={"i": offset_seconds}),
        created_at=BASE + timedelta(seconds=offset_seconds),
    )
    return record.id


def test_list_events_after_returns_only_newer_events(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _append(store, offset_seconds=0)
    cursor_id = _append(store, offset_seconds=10)
    _append(store, offset_seconds=20)
    _append(store, offset_seconds=30)

    after = store.list_events_after(
        BASE + timedelta(seconds=10), cursor_id, limit=100
    )
    assert [event.payload["i"] for event in after] == [20, 30]


def test_none_cursor_reads_from_start(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    _append(store, offset_seconds=0)
    _append(store, offset_seconds=10)

    after = store.list_events_after(None, None, limit=100)
    assert len(after) == 2


def test_limit_is_respected_and_ordered(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    for offset in (30, 0, 20, 10):
        _append(store, offset_seconds=offset)

    after = store.list_events_after(None, None, limit=2)
    assert [event.payload["i"] for event in after] == [0, 10]


def test_same_timestamp_uses_id_tie_breaker(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    a = store.append_event(
        EventCreate(event_type="user_prompt", source="test", payload={"n": "a"}),
        created_at=BASE,
    )
    b = store.append_event(
        EventCreate(event_type="user_prompt", source="test", payload={"n": "b"}),
        created_at=BASE,
    )
    # Event ids are random; the cursor must advance by id within the same second.
    first, second = sorted([a, b], key=lambda event: event.id)

    after = store.list_events_after(BASE, first.id, limit=100)
    assert [event.id for event in after] == [second.id]


def _segment(index: int) -> SessionSegmentRecord:
    return SessionSegmentRecord(
        id=f"seg_{index}",
        project="proj",
        session_id="sess",
        segment_index=index,
        first_event_at=BASE,
        last_event_at=BASE,
        event_count=1,
        status="open",
    )


def test_get_latest_segment_returns_highest_index(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    store.upsert_session_segment(_segment(0))
    store.upsert_session_segment(_segment(2))
    store.upsert_session_segment(_segment(1))

    latest = store.get_latest_segment_for_session("proj", "sess")
    assert latest is not None
    assert latest.segment_index == 2


def test_get_latest_segment_unknown_session_is_none(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    assert store.get_latest_segment_for_session("proj", "missing") is None
