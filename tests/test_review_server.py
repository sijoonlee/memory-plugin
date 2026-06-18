from __future__ import annotations

from datetime import datetime, timezone

from starlette.testclient import TestClient

from memory_mcp.core.events import (
    EventCreate,
    EventStore,
    MemoryCandidateCreate,
    SessionSegmentRecord,
)
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.review.server import create_app

from conftest import FakeEmbedder


def test_review_server_lists_and_rejects_candidate(tmp_path) -> None:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    candidate = event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests.",
            lesson="Use the repo environment.",
            action="Use uv run pytest.",
            category="testing",
            evidence_summary="User correction.",
            creation_reason="User correction.",
        )
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
    event_store = EventStore(root)
    candidate = event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests.",
            lesson="Use pytest.",
            action="Run pytest.",
            category="testing",
            evidence_summary="User correction.",
            creation_reason="User correction.",
        )
    )
    client = TestClient(create_app(root))

    response = client.patch(
        f"/api/candidates/{candidate.id}",
        json={"lesson": "Use uv for tests.", "confidence": 0.75},
    )

    assert response.status_code == 200
    payload = response.json()["candidate"]
    assert payload["lesson"] == "Use uv for tests."
    assert payload["confidence"] == 0.75


def test_review_server_lists_active_memories_readonly(tmp_path) -> None:
    root = tmp_path / "memory"
    record = LocalMemoryStore(root, FakeEmbedder()).create_memory(
        MemoryCreate(
            what_happened="pytest used the wrong environment.",
            when_useful="When running tests in this repo.",
            helpful_explanation="Use uv run pytest.",
            tags=["testing"],
        )
    )
    client = TestClient(create_app(root))

    listed = client.get("/api/memories").json()
    assert [memory["id"] for memory in listed["memories"]] == [record.id]

    detail = client.get(f"/api/memories/{record.id}").json()
    assert detail["memory"]["what_happened"] == "pytest used the wrong environment."

    # Active memories are read-only: the detail route rejects writes (405) and
    # no approve/reject mutation routes exist (404).
    assert client.patch(f"/api/memories/{record.id}", json={}).status_code == 405
    assert client.post(f"/api/memories/{record.id}/approve", json={}).status_code == 404


def test_review_server_reject_requires_reason(tmp_path) -> None:
    root = tmp_path / "memory"
    event_store = EventStore(root)
    candidate = event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests.",
            lesson="Use pytest.",
            action="Run pytest.",
            category="testing",
            evidence_summary="User correction.",
            creation_reason="User correction.",
        )
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
