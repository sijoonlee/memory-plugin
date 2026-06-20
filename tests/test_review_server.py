from __future__ import annotations

from datetime import datetime, timezone

from starlette.testclient import TestClient

from memory_mcp.core.events import (
    EventCreate,
    EventStore,
    SessionSegmentRecord,
)
from memory_mcp.core.models import MemoryCreate, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.review.server import create_app

from conftest import FakeEmbedder


def _seed_pending(root, *, when_useful: str, details: str, memory_type: str = "feedback"):
    store = LocalMemoryStore(root, FakeEmbedder())
    return store.create_pending(
        MemoryCreate(
            when_useful=when_useful,
            details=details,
            memory_type=memory_type,
            source=MemorySource(
                kind="pipeline_candidate",
                creation_reason="User correction.",
                extra={"evidence_summary": "User correction."},
            ),
        )
    )


def test_review_server_lists_and_rejects_candidate(tmp_path) -> None:
    root = tmp_path / "memory"
    candidate = _seed_pending(
        root,
        when_useful="When running tests.",
        details="Use the repo environment. Use uv run pytest.",
    )
    client = TestClient(create_app(root))

    listed = client.get("/api/candidates").json()
    assert listed["candidates"][0]["id"] == candidate.id

    response = client.post(
        f"/api/candidates/{candidate.id}/reject",
        json={"reason": "Too specific."},
    )

    assert response.status_code == 200
    assert response.json()["candidate"]["status"] == "rejected"


def test_review_server_updates_candidate(tmp_path) -> None:
    root = tmp_path / "memory"
    candidate = _seed_pending(
        root,
        when_useful="When running tests.",
        details="Use pytest. Run pytest.",
    )
    client = TestClient(create_app(root))

    response = client.patch(
        f"/api/candidates/{candidate.id}",
        json={"details": "Use uv for tests.", "confidence": 0.75},
    )

    assert response.status_code == 200
    payload = response.json()["candidate"]
    assert payload["details"] == "Use uv for tests."
    assert payload["confidence"] == 0.75


def test_review_server_lists_active_memories(tmp_path) -> None:
    root = tmp_path / "memory"
    record = LocalMemoryStore(root, FakeEmbedder()).create_memory(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details="pytest used the wrong environment. Use uv run pytest.",
            tags=["testing"],
        )
    )
    client = TestClient(create_app(root))

    listed = client.get("/api/memories").json()
    assert [memory["id"] for memory in listed["memories"]] == [record.id]

    detail = client.get(f"/api/memories/{record.id}").json()
    assert detail["memory"]["details"].startswith("pytest used the wrong environment.")


def test_review_server_memory_manager_actions(tmp_path) -> None:
    root = tmp_path / "memory"
    record = LocalMemoryStore(root, FakeEmbedder()).create_memory(
        MemoryCreate(
            when_useful="When configuring CI caching.",
            details="Cache the uv directory between runs.",
            tags=["ci"],
        )
    )
    client = TestClient(create_app(root))

    # Starts unread → shows in the unread inbox.
    inbox = client.get("/api/memories?status=active&is_reviewed=false").json()
    assert [m["id"] for m in inbox["memories"]] == [record.id]

    # Mark read.
    reviewed = client.post(
        f"/api/memories/{record.id}/reviewed", json={"value": True}
    ).json()
    assert reviewed["memory"]["is_reviewed"] is True
    assert client.get("/api/memories?status=active&is_reviewed=false").json()["memories"] == []

    # Archive (soft delete) → out of active, into archived.
    archived = client.post(f"/api/memories/{record.id}/archive").json()
    assert archived["memory"]["status"] == "archived"
    assert client.get("/api/memories?status=active").json()["memories"] == []
    assert [m["id"] for m in client.get("/api/memories?status=archived").json()["memories"]] == [record.id]

    # Restore, then hard delete.
    assert client.post(f"/api/memories/{record.id}/restore").json()["memory"]["status"] == "active"
    deleted = client.request("DELETE", f"/api/memories/{record.id}").json()
    assert deleted == {"deleted": True, "memory_id": record.id}
    assert client.get("/api/memories?status=active").json()["memories"] == []


def test_review_server_reject_requires_reason(tmp_path) -> None:
    root = tmp_path / "memory"
    candidate = _seed_pending(
        root,
        when_useful="When running tests.",
        details="Use pytest. Run pytest.",
    )
    client = TestClient(create_app(root))

    response = client.post(
        f"/api/candidates/{candidate.id}/reject",
        json={"reason": ""},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "reason is required"


def test_review_server_lists_segments_and_event_log(tmp_path) -> None:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    event_store.upsert_session_segment(
        SessionSegmentRecord(
            id="seg_skipped",
            project="/repo",
            session_id="s1",
            segment_index=0,
            first_event_at=datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc),
            last_event_at=datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc),
            event_count=1,
            status="skipped",
            error="No durable memory candidate found.",
        )
    )
    event_store.append_event(
        EventCreate(
            event_type="tool_result",
            source="test",
            project="/repo",
            session_id="s1",
            payload={"message": "ran pytest"},
        ),
        created_at=datetime(2026, 6, 14, 0, 30, tzinfo=timezone.utc),
    )
    client = TestClient(create_app(root))

    listed = client.get("/api/segments?status=skipped").json()
    assert listed["status"] == "skipped"
    assert listed["total"] == 1
    assert listed["segments"][0]["id"] == "seg_skipped"
    assert listed["segments"][0]["error"] == "No durable memory candidate found."

    detail = client.get("/api/segments/seg_skipped/events").json()
    assert detail["segment"]["id"] == "seg_skipped"
    assert [event["payload"]["message"] for event in detail["events"]] == ["ran pytest"]


def test_review_server_segment_events_missing_returns_400(tmp_path) -> None:
    root = tmp_path / "memory"
    EventStore(root)
    client = TestClient(create_app(root))

    response = client.get("/api/segments/seg_nope/events")

    assert response.status_code == 400
    assert "session segment not found" in response.json()["error"]
