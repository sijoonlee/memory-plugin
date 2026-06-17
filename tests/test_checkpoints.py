from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory_mcp.core.events import EventStore, SessionSegmentRecord
from memory_mcp.core.store import LocalMemoryStore

from conftest import FakeEmbedder


def _segment(segment_id: str) -> SessionSegmentRecord:
    now = datetime(2026, 6, 16, tzinfo=timezone.utc)
    return SessionSegmentRecord(
        id=segment_id,
        project="proj",
        session_id="sess",
        segment_index=0,
        first_event_at=now,
        last_event_at=now,
        event_count=1,
        status="open",
    )


def test_event_store_checkpoint_round_trip(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    assert store.get_checkpoint("session_worker") is None

    store.set_checkpoint("session_worker", "cursor-1")
    assert store.get_checkpoint("session_worker") == "cursor-1"

    store.set_checkpoint("session_worker", "cursor-2")
    assert store.get_checkpoint("session_worker") == "cursor-2"


def test_memory_store_checkpoint_still_works(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    assert store.get_checkpoint("daily_decay_date") is None
    store.set_checkpoint("daily_decay_date", "2026-06-16")
    assert store.get_checkpoint("daily_decay_date") == "2026-06-16"


def test_transaction_commits_segment_and_checkpoint_together(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    with store.transaction() as conn:
        store.upsert_session_segment(_segment("seg_atomic"), conn=conn)
        store.set_checkpoint("session_worker", "cursor-1", conn=conn)

    assert store.get_session_segment("seg_atomic") is not None
    assert store.get_checkpoint("session_worker") == "cursor-1"


def test_transaction_rolls_back_on_error(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            store.upsert_session_segment(_segment("seg_rollback"), conn=conn)
            store.set_checkpoint("session_worker", "cursor-1", conn=conn)
            raise RuntimeError("boom")

    # Neither the segment nor the checkpoint should have been committed.
    assert store.get_session_segment("seg_rollback") is None
    assert store.get_checkpoint("session_worker") is None


def test_new_indexes_exist(tmp_path) -> None:
    store = EventStore(tmp_path / "events")
    with store._connect() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    assert "idx_events_created_id" in names
    assert "idx_session_segments_session" in names
