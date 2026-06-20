from __future__ import annotations

from starlette.testclient import TestClient

from memory_mcp.core.models import MemoryCreate, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.review.server import create_app

from conftest import FakeEmbedder


def _seed_candidate(
    root,
    *,
    lesson: str,
    evidence: list[str],
    segment: str,
) -> str:
    store = LocalMemoryStore(root, FakeEmbedder())
    record = store.create_pending(
        MemoryCreate(
            when_useful="When running tests.",
            details=f"{lesson} Use uv run pytest.",
            tags=["testing"],
            source=MemorySource(
                kind="pipeline_candidate",
                evidence_event_ids=evidence,
                creation_reason="User correction.",
                extra={
                    "evidence_summary": "User correction.",
                    "category": "testing",
                    "source_session_segment_id": segment,
                },
            ),
        )
    )
    return record.id


_MERGED_BODY = {
    "when_useful": "When running tests.",
    "details": "Run tests through the project environment. Use uv run pytest so dependencies resolve.",
    "tags": ["testing"],
    "confidence": 0.8,
    "source": {
        "kind": "pipeline_merge",
        "creation_reason": "Merged from repeated corrections.",
        "extra": {
            "evidence_summary": "User corrected the test command across sessions.",
            "category": "testing",
        },
    },
}


def test_merge_endpoint_creates_pending_candidate_and_marks_sources(tmp_path) -> None:
    root = tmp_path / "memory"
    a = _seed_candidate(root, lesson="pytest fails.", evidence=["evt_1"], segment="seg_a")
    b = _seed_candidate(root, lesson="dep errors.", evidence=["evt_2"], segment="seg_b")
    client = TestClient(create_app(root))

    response = client.post(
        "/api/candidates/merge",
        json={"source_ids": [a, b], "merged": _MERGED_BODY},
    )

    assert response.status_code == 200
    merged = response.json()["candidate"]
    assert merged["status"] == "pending_review"
    assert merged["source"]["evidence_event_ids"] == ["evt_1", "evt_2"]
    assert merged["source"]["extra"]["merged_from"]["source_candidate_ids"] == [a, b]

    # Sources are no longer in the pending queue.
    pending = client.get("/api/candidates").json()["candidates"]
    pending_ids = {c["id"] for c in pending}
    assert a not in pending_ids and b not in pending_ids
    assert merged["id"] in pending_ids


def test_merge_endpoint_reports_validation_error(tmp_path) -> None:
    root = tmp_path / "memory"
    a = _seed_candidate(root, lesson="only one.", evidence=["evt_1"], segment="seg_a")
    client = TestClient(create_app(root))

    response = client.post(
        "/api/candidates/merge",
        json={"source_ids": [a], "merged": _MERGED_BODY},
    )

    assert response.status_code == 400
    assert "at least two distinct" in response.json()["error"]


def test_archive_endpoint_hides_candidate(tmp_path) -> None:
    root = tmp_path / "memory"
    a = _seed_candidate(root, lesson="noisy.", evidence=["evt_1"], segment="seg_a")
    client = TestClient(create_app(root))

    response = client.post(f"/api/candidates/{a}/archive")
    assert response.status_code == 200
    assert response.json()["candidate"]["status"] == "archived"

    pending = client.get("/api/candidates").json()["candidates"]
    assert a not in {c["id"] for c in pending}
    archived = client.get("/api/candidates?status=archived").json()["candidates"]
    assert [c["id"] for c in archived] == [a]
