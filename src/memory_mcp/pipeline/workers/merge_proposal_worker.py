from __future__ import annotations

from dataclasses import dataclass

from memory_mcp.core.events import MemoryCandidateCreate, MemoryCandidateRecord
from memory_mcp.pipeline.extractors import MergeProposer
from memory_mcp.pipeline.workers.candidate_worker import CandidateWorker

_STOP_WORDS = {"a", "an", "and", "or", "the", "to", "of", "in", "this"}


@dataclass(frozen=True)
class MergeProposalWorkerResult:
    clusters_found: int
    proposals_created: int
    declined: int


class MergeProposalWorker:
    """Cluster similar pending candidates and ask an LLM to propose merges.

    The clustering is deterministic (category match plus lexical overlap). The
    LLM only proposes merged content; the actual merge is performed through the
    human-gated 13A primitive (`CandidateWorker.merge_candidates`), which yields
    a new `pending_review` candidate. The worker never creates active memories —
    a human still approves the merged candidate.
    """

    def __init__(
        self,
        *,
        candidate_worker: CandidateWorker,
        proposer: MergeProposer,
        similarity_threshold: float = 0.5,
        min_cluster_size: int = 2,
    ) -> None:
        self.candidate_worker = candidate_worker
        self.proposer = proposer
        self.similarity_threshold = similarity_threshold
        self.min_cluster_size = min_cluster_size

    def run_once(self, *, limit: int = 1) -> MergeProposalWorkerResult:
        pending = self.candidate_worker.list_candidates(status="pending_review")
        clusters = [
            cluster
            for cluster in _cluster_candidates(pending, self.similarity_threshold)
            if len(cluster) >= self.min_cluster_size
        ]

        created = 0
        declined = 0
        for cluster in clusters[:limit]:
            proposal = self.proposer.propose(candidates=cluster)
            if not proposal.should_merge:
                declined += 1
                continue
            self.candidate_worker.merge_candidates(
                [candidate.id for candidate in cluster],
                MemoryCandidateCreate(
                    situation=proposal.situation,
                    lesson=proposal.lesson,
                    action=proposal.action,
                    category=proposal.category or cluster[0].category,
                    confidence=proposal.confidence,
                    creation_reason="LLM-proposed merge of related candidates.",
                    evidence_summary=proposal.evidence_summary,
                    metadata={
                        "proposer": "llm",
                        "merge_proposal_reason": proposal.reason,
                    },
                ),
            )
            created += 1

        return MergeProposalWorkerResult(
            clusters_found=len(clusters),
            proposals_created=created,
            declined=declined,
        )


def _cluster_candidates(
    candidates: list[MemoryCandidateRecord],
    threshold: float,
) -> list[list[MemoryCandidateRecord]]:
    """Single-linkage cluster by shared category and lexical overlap."""

    count = len(candidates)
    parent = list(range(count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        parent[find(left)] = find(right)

    tokens = [_candidate_tokens(candidate) for candidate in candidates]
    for i in range(count):
        for j in range(i + 1, count):
            if candidates[i].category != candidates[j].category:
                continue
            if _jaccard(tokens[i], tokens[j]) >= threshold:
                union(i, j)

    groups: dict[int, list[MemoryCandidateRecord]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(candidates[index])
    return list(groups.values())


def _candidate_tokens(candidate: MemoryCandidateRecord) -> set[str]:
    return _tokens(f"{candidate.situation} {candidate.lesson} {candidate.action}")


def _tokens(text: str) -> set[str]:
    normalized = "".join(char if char.isalnum() else " " for char in text.lower())
    return {
        token
        for token in normalized.split()
        if token and token not in _STOP_WORDS
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
