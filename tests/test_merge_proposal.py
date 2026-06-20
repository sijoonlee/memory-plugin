from __future__ import annotations

from memory_mcp.core.events import EventStore
from memory_mcp.core.models import MemoryCreate, MemorySource
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.extractors import MergeProposalResult, StaticMergeProposer
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker
from memory_mcp.pipeline.workers.merge_proposal_worker import (
    MergeProposalWorker,
    _cluster_candidates,
)

from conftest import FakeEmbedder


def _worker(tmp_path, proposer) -> tuple[MergeProposalWorker, CandidateWorker, LocalMemoryStore]:
    event_store = EventStore(tmp_path / "events")
    memory_store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    candidate_worker = CandidateWorker(event_store=event_store, memory_store=memory_store)
    worker = MergeProposalWorker(
        candidate_worker=candidate_worker,
        proposer=proposer,
        similarity_threshold=0.3,
    )
    return worker, candidate_worker, memory_store


def _seed(worker: CandidateWorker, *, lesson: str, action: str, category: str, evidence: list[str]) -> str:
    record = worker.create_candidate(
        MemoryCreate(
            when_useful="When running tests in this repo.",
            details=f"{lesson} {action}",
            tags=[category],
            confidence=0.7,
            source=MemorySource(
                kind="pipeline_candidate",
                evidence_event_ids=evidence,
                creation_reason="Extracted from idle session segment by LLM.",
                extra={
                    "source_session_segment_id": f"seg_{evidence[0]}",
                    "evidence_summary": "A test-command correction.",
                    "category": category,
                },
            ),
        )
    )
    return record.id


def _merge_result() -> MergeProposalResult:
    return MergeProposalResult(
        should_merge=True,
        reason="All three describe running tests through the project environment.",
        situation="When running tests in this repo.",
        lesson="Run tests through the project environment.",
        action="Use uv run pytest so dependencies resolve.",
        category="durable_workflow",
        confidence=0.85,
        evidence_summary="The user corrected the test command across sessions.",
    )


def test_agent_merges_cluster_into_pending_candidate_without_active_memory(tmp_path) -> None:
    worker, candidates, memory_store = _worker(tmp_path, StaticMergeProposer(_merge_result()))
    a = _seed(candidates, lesson="pytest fails directly in tests.", action="use uv run pytest", category="durable_workflow", evidence=["evt_1"])
    b = _seed(candidates, lesson="pytest dependency errors in tests.", action="use uv run pytest tests", category="durable_workflow", evidence=["evt_2"])

    result = worker.run_once(limit=5)

    assert result.proposals_created == 1
    # The safety invariant: the agent created no active memory.
    assert memory_store.list_memories(status="active") == []

    # The merged proposal is a normal pending_review candidate, still human-gated.
    pending = candidates.list_candidates(status="pending_review")
    assert len(pending) == 1
    merged = pending[0]
    assert merged.details.startswith("Run tests through the project environment.")
    assert merged.source.extra["merged_from"]["source_candidate_ids"] == [a, b]
    assert merged.source.extra["merge_proposal_reason"]

    # Sources are marked merged (reversible), not approved.
    assert candidates.get_candidate(a).status == "merged"
    assert candidates.get_candidate(b).status == "merged"


def test_agent_declines_when_proposal_says_no(tmp_path) -> None:
    decline = MergeProposalResult(should_merge=False, reason="These are distinct lessons.")
    worker, candidates, memory_store = _worker(tmp_path, StaticMergeProposer(decline))
    a = _seed(candidates, lesson="pytest fails in tests.", action="use uv run pytest", category="durable_workflow", evidence=["evt_1"])
    b = _seed(candidates, lesson="pytest errors in tests.", action="use uv run pytest now", category="durable_workflow", evidence=["evt_2"])

    result = worker.run_once(limit=5)

    assert result.proposals_created == 0
    assert result.declined == 1
    # Sources untouched, still pending.
    assert candidates.get_candidate(a).status == "pending_review"
    assert candidates.get_candidate(b).status == "pending_review"
    assert memory_store.list_memories(status="active") == []


def test_dissimilar_candidates_are_not_clustered(tmp_path) -> None:
    worker, candidates, _ = _worker(tmp_path, StaticMergeProposer(_merge_result()))
    _seed(candidates, lesson="run tests with uv.", action="uv run pytest", category="durable_workflow", evidence=["evt_1"])
    _seed(candidates, lesson="deploy uses a blue green flip.", action="run the deploy script", category="external_context", evidence=["evt_2"])

    result = worker.run_once(limit=5)

    # No cluster of size >= 2, so nothing is proposed.
    assert result.clusters_found == 0
    assert result.proposals_created == 0
    assert len(candidates.list_candidates(status="pending_review")) == 2


def test_cluster_groups_only_same_category_overlap(tmp_path) -> None:
    worker, candidates, _ = _worker(tmp_path, StaticMergeProposer(_merge_result()))
    a = _seed(candidates, lesson="run tests with uv pytest.", action="uv run pytest", category="durable_workflow", evidence=["evt_1"])
    b = _seed(candidates, lesson="run tests using uv pytest.", action="uv run pytest tests", category="durable_workflow", evidence=["evt_2"])
    # Same words but a different category must not join the cluster.
    _seed(candidates, lesson="run tests with uv pytest.", action="uv run pytest", category="repeated_pitfall", evidence=["evt_3"])

    pending = candidates.list_candidates(status="pending_review")
    clusters = [c for c in _cluster_candidates(pending, 0.5) if len(c) >= 2]
    assert len(clusters) == 1
    assert {c.id for c in clusters[0]} == {a, b}
