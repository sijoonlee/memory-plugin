from __future__ import annotations

from datetime import datetime, timezone

from memory_mcp.core.events import EventCreate, EventStore, MemoryCandidateCreate
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.mcp_server import service
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker
from memory_mcp.pipeline.workers.session_worker import SessionWorker

from conftest import FakeEmbedder


def _seed_scoped_memories(store: LocalMemoryStore) -> dict[str, str]:
    """Three retrievable but lexically distinct memories across two repos + global."""

    alpha = store.create_memory(
        MemoryCreate(
            what_happened="Alpha gateway pods crashed during rollout.",
            when_useful="When deploying sdk changes for alpha.",
            helpful_explanation="Restart alpha pods after the migration.",
            tags=["alpha"],
            project="/repos/alpha",
        )
    )
    beta = store.create_memory(
        MemoryCreate(
            what_happened="Beta cache returned stale entries.",
            when_useful="When deploying sdk changes for beta.",
            helpful_explanation="Flush beta cache before each release.",
            tags=["beta"],
            project="/repos/beta",
        )
    )
    glob = store.create_memory(
        MemoryCreate(
            what_happened="Gamma logging dropped structured fields.",
            when_useful="When deploying sdk changes for gamma.",
            helpful_explanation="Enable gamma json formatter.",
            tags=["gamma"],
        )
    )
    return {"alpha": alpha.id, "beta": beta.id, "global": glob.id}


def test_search_is_inclusive_repo_plus_global(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    ids = _seed_scoped_memories(store)

    results = store.search_memories("sdk deploying changes", limit=10, project="/repos/alpha")
    found = {result.memory.id for result in results}

    assert ids["alpha"] in found
    assert ids["global"] in found
    assert ids["beta"] not in found


def test_search_without_project_returns_all(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    ids = _seed_scoped_memories(store)

    results = store.search_memories("sdk deploying changes", limit=10)
    found = {result.memory.id for result in results}

    assert found == set(ids.values())


def test_list_memories_filters_by_project(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    ids = _seed_scoped_memories(store)

    scoped = store.list_memories(project="/repos/beta")
    assert [record.id for record in scoped] == [ids["beta"]]


def test_manual_create_sets_project(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    response = service.memory_create(
        store,
        what_happened="Lesson body.",
        when_useful="Situation.",
        helpful_explanation="Action.",
        project="/repos/manual",
    )
    record_id = response["memory"]["id"]

    stored = store.get_memory(record_id)
    assert stored is not None
    assert stored.project == "/repos/manual"


def test_search_event_context_scopes_retrieval(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    ids = _seed_scoped_memories(store)
    event_store = EventStore(tmp_path / "memory")

    response = service.memory_search(
        store,
        "sdk deploying changes",
        limit=10,
        event_store=event_store,
        event_context={"project": "/repos/alpha"},
    )
    returned = {memory["id"] for memory in response["memories"]}

    assert ids["alpha"] in returned
    assert ids["global"] in returned
    assert ids["beta"] not in returned


def test_approval_carries_project_from_segment(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())

    start = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repos/scoped",
            session_id="session-1",
            payload={"text": "hello"},
        ),
        created_at=start,
    )
    SessionWorker(event_store=event_store, idle_after_seconds=0).run_once(now=start)
    segment = event_store.list_session_segments()[0]
    assert segment.project == "/repos/scoped"

    worker = CandidateWorker(event_store=event_store, memory_store=memory_store)
    candidate = event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="When running tests in this repo.",
            lesson="Direct pytest uses the wrong environment.",
            action="Use uv run pytest.",
            category="durable_workflow",
            confidence=0.8,
            creation_reason="User correction in session.",
            evidence_event_ids=["evt_1"],
            evidence_summary="The user corrected the test command.",
            source_session_segment_id=segment.id,
        )
    )

    _, memory = worker.approve_candidate(candidate.id)

    assert memory.project == "/repos/scoped"


def test_merged_candidate_approves_as_global(tmp_path) -> None:
    """A merged candidate has no single source segment, so it approves as global."""

    event_store = EventStore(tmp_path / "memory")
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    worker = CandidateWorker(event_store=event_store, memory_store=memory_store)
    candidate = event_store.create_memory_candidate(
        MemoryCandidateCreate(
            situation="Situation.",
            lesson="Lesson.",
            action="Action.",
            category="durable_workflow",
            confidence=0.8,
            creation_reason="merge",
            evidence_event_ids=["evt_1"],
            evidence_summary="summary",
            source_session_segment_id=None,
        )
    )

    _, memory = worker.approve_candidate(candidate.id)

    assert memory.project is None
