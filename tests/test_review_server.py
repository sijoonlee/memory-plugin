from __future__ import annotations

from starlette.testclient import TestClient

from memory_mcp.core.events import EventStore, MemoryCandidateCreate
from memory_mcp.review.server import create_app


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
